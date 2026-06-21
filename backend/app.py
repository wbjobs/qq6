import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import csv
import time
from datetime import datetime
from typing import List, Dict, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from interpolation.kriging import (
    KrigingInterpolator,
    ROOM_WIDTH,
    ROOM_HEIGHT,
    compute_aging_weights,
    AGING_GRACE_PERIOD,
    AGING_DECAY_RATE,
    AGING_WEIGHT_THRESHOLD,
)
from interpolation.source_trace import trace_pollution_source


class SensorReading(BaseModel):
    id: str
    name: str
    x: float = Field(ge=0, le=ROOM_WIDTH)
    y: float = Field(ge=0, le=ROOM_HEIGHT)
    pm25: float = Field(ge=0)
    co2: float = Field(ge=0)
    temperature: float
    humidity: float = Field(ge=0, le=100)
    timestamp: Optional[str] = None


class SensorBatch(BaseModel):
    sensors: List[SensorReading]


class MQTTConfig(BaseModel):
    broker: str = "localhost"
    port: int = 1883
    topic: str = "sensors/#"
    enabled: bool = False


class AlertRule(BaseModel):
    id: str
    name: str
    metric: str = Field(pattern="^(pm25|co2|temperature|humidity)$")
    operator: str = Field(pattern="^(>|<|>=|<=|==)$")
    threshold: float
    consecutive_count: int = Field(default=3, ge=1, le=10)
    enabled: bool = True


class AlertRuleCreate(BaseModel):
    name: str
    metric: str = Field(pattern="^(pm25|co2|temperature|humidity)$")
    operator: str = Field(pattern="^(>|<|>=|<=|==)$")
    threshold: float
    consecutive_count: int = Field(default=3, ge=1, le=10)
    enabled: bool = True


class AlertEvent(BaseModel):
    rule_id: str
    rule_name: str
    metric: str
    operator: str
    threshold: float
    actual_value: float
    sensor_id: str
    sensor_name: str
    consecutive_hits: int
    timestamp: str



class AppState:
    def __init__(self):
        self.sensors: Dict[str, Dict] = {}
        self.interpolator = KrigingInterpolator()
        self.simulator_task: Optional[asyncio.Task] = None
        self.simulator_running = False
        self.mqtt_enabled = False
        self.mqtt_client = None
        self.simulator = None
        self.sensor_last_update: Dict[str, datetime] = {}
        self.alert_rules: Dict[str, Dict] = {}
        self.consecutive_counters: Dict[str, Dict[str, int]] = {}
        self.alert_events: List[Dict] = []
        self._alert_rule_id_seq = 0

    def update_sensor(self, reading: SensorReading) -> None:
        if reading.timestamp is None:
            reading.timestamp = datetime.now().isoformat()
        self.sensors[reading.id] = reading.model_dump()
        self.sensor_last_update[reading.id] = datetime.now()

    def get_aging_weights(self, sensor_ids: List[str]) -> List[float]:
        timestamps = [
            self.sensors[sid].get("timestamp") if sid in self.sensors else None
            for sid in sensor_ids
        ]
        return compute_aging_weights(timestamps)

    def get_active_sensor_ids(self) -> List[str]:
        all_ids = list(self.sensors.keys())
        if not all_ids:
            return []
        weights = self.get_aging_weights(all_ids)
        return [sid for sid, w in zip(all_ids, weights) if w >= AGING_WEIGHT_THRESHOLD]

    def _next_rule_id(self) -> str:
        self._alert_rule_id_seq += 1
        return f"rule_{self._alert_rule_id_seq:04d}"

    def add_alert_rule(self, rule: AlertRuleCreate) -> Dict:
        rule_id = self._next_rule_id()
        rule_dict = rule.model_dump()
        rule_dict["id"] = rule_id
        rule_dict["created_at"] = datetime.now().isoformat()
        self.alert_rules[rule_id] = rule_dict
        return rule_dict

    def update_alert_rule(self, rule_id: str, update: AlertRuleCreate) -> Optional[Dict]:
        if rule_id not in self.alert_rules:
            return None
        self.alert_rules[rule_id].update(update.model_dump())
        self.alert_rules[rule_id]["id"] = rule_id
        return self.alert_rules[rule_id]

    def delete_alert_rule(self, rule_id: str) -> bool:
        if rule_id in self.alert_rules:
            del self.alert_rules[rule_id]
            return True
        return False

    @staticmethod
    def _compare(value: float, operator: str, threshold: float) -> bool:
        if operator == ">":
            return value > threshold
        if operator == "<":
            return value < threshold
        if operator == ">=":
            return value >= threshold
        if operator == "<=":
            return value <= threshold
        if operator == "==":
            return abs(value - threshold) < 1e-6
        return False

    def check_alert_rules(self) -> List[Dict]:
        triggered: List[Dict] = []
        now = datetime.now().isoformat()

        for rule_id, rule in self.alert_rules.items():
            if not rule.get("enabled", True):
                continue
            metric = rule["metric"]
            operator = rule["operator"]
            threshold = rule["threshold"]
            required = rule.get("consecutive_count", 3)
            rule_name = rule.get("name", rule_id)

            if rule_id not in self.consecutive_counters:
                self.consecutive_counters[rule_id] = {}

            for sid, sensor in self.sensors.items():
                value = sensor.get(metric)
                if value is None:
                    continue
                is_violation = self._compare(value, operator, threshold)
                counter_key = sid

                if is_violation:
                    current = self.consecutive_counters[rule_id].get(counter_key, 0) + 1
                    self.consecutive_counters[rule_id][counter_key] = current

                    if current >= required:
                        event = {
                            "rule_id": rule_id,
                            "rule_name": rule_name,
                            "metric": metric,
                            "operator": operator,
                            "threshold": threshold,
                            "actual_value": float(value),
                            "sensor_id": sid,
                            "sensor_name": sensor.get("name", sid),
                            "consecutive_hits": current,
                            "timestamp": now,
                        }
                        triggered.append(event)
                        self.consecutive_counters[rule_id][counter_key] = 0
                        self.alert_events.insert(0, event)
                        if len(self.alert_events) > 100:
                            self.alert_events.pop()
                else:
                    self.consecutive_counters[rule_id][counter_key] = 0

        return triggered


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if state.simulator_task and not state.simulator_task.done():
        state.simulator_task.cancel()
    if state.mqtt_client:
        try:
            state.mqtt_client.loop_stop()
            state.mqtt_client.disconnect()
        except Exception:
            pass


app = FastAPI(
    title="室内空气质量监测系统 API",
    description="基于克里金插值的室内空气质量监测后端",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "sensors_count": len(state.sensors),
        "simulator_running": state.simulator_running,
        "mqtt_enabled": state.mqtt_enabled,
    }


@app.get("/sensors")
async def get_sensors():
    sensors_list = list(state.sensors.values())
    return sensors_list


@app.get("/sensors/{sensor_id}")
async def get_sensor(sensor_id: str):
    if sensor_id not in state.sensors:
        raise HTTPException(status_code=404, detail="传感器不存在")
    return state.sensors[sensor_id]


@app.post("/sensors")
async def add_sensor(reading: SensorReading):
    state.update_sensor(reading)
    return {"status": "success", "sensor": state.sensors[reading.id]}


@app.post("/sensors/batch")
async def add_sensors_batch(batch: SensorBatch):
    added = []
    for reading in batch.sensors:
        state.update_sensor(reading)
        added.append(reading.id)
    return {"status": "success", "added_count": len(added), "added_ids": added}


@app.post("/sensors/clear")
async def clear_sensors():
    state.sensors.clear()
    state.sensor_last_update.clear()
    return {"status": "success", "message": "所有传感器数据已清除"}


@app.post("/sensors/import/csv")
async def import_sensors_csv(file: UploadFile = File(...)):
    try:
        content = await file.read()
        decoded = content.decode("utf-8")
        reader = csv.DictReader(decoded.splitlines())

        added = 0
        for row in reader:
            try:
                reading = SensorReading(
                    id=row["id"],
                    name=row.get("name", row["id"]),
                    x=float(row["x"]),
                    y=float(row["y"]),
                    pm25=float(row["pm25"]),
                    co2=float(row["co2"]),
                    temperature=float(row["temperature"]),
                    humidity=float(row["humidity"]),
                    timestamp=row.get("timestamp") or datetime.now().isoformat(),
                )
                state.update_sensor(reading)
                added += 1
            except (KeyError, ValueError) as e:
                continue

        return {"status": "success", "imported_count": added}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV导入失败: {str(e)}")


@app.get("/interpolate")
async def interpolate(
    metric: str = Query(
        default="pm25",
        pattern="^(pm25|co2|temperature|humidity)$",
        description="插值的指标类型",
    ),
    resolution: int = Query(
        default=40, ge=10, le=100, description="网格分辨率 (N x N)"
    ),
):
    sensors = list(state.sensors.values())
    if len(sensors) == 0:
        return {
            "grid": [[0.0] * resolution for _ in range(resolution)],
            "variance": [],
            "min_val": 0.0,
            "max_val": 0.0,
            "mean_val": 0.0,
            "metric": metric,
            "resolution": resolution,
            "sensors_count": 0,
            "sensors": [],
            "aging_weights": [],
            "active_sensors": [],
        }

    sensor_ids = list(state.sensors.keys())
    aging_weights = state.get_aging_weights(sensor_ids)

    positions = [(s["x"], s["y"]) for s in sensors]
    values = [s[metric] for s in sensors]

    result = state.interpolator.interpolate(
        sensor_positions=positions,
        sensor_values=values,
        resolution=resolution,
        room_width=ROOM_WIDTH,
        room_height=ROOM_HEIGHT,
        aging_weights=aging_weights,
    )

    active_sensor_ids = state.get_active_sensor_ids()
    active_sensors = [
        {**s, "aging_weight": w, "is_active": sid in active_sensor_ids}
        for s, sid, w in zip(sensors, sensor_ids, aging_weights)
    ]

    result["metric"] = metric
    result["resolution"] = resolution
    result["sensors_count"] = len(sensors)
    result["sensors"] = active_sensors
    result["aging_weights"] = aging_weights
    result["active_sensors"] = active_sensor_ids

    return result


@app.get("/point")
async def get_point_value(
    x: float = Query(ge=0, le=ROOM_WIDTH, description="X坐标 (米)"),
    y: float = Query(ge=0, le=ROOM_HEIGHT, description="Y坐标 (米)"),
):
    sensors = list(state.sensors.values())
    if len(sensors) == 0:
        return {
            "x": x,
            "y": y,
            "pm25": 0.0,
            "co2": 0.0,
            "temperature": 0.0,
            "humidity": 0.0,
        }

    sensor_ids = list(state.sensors.keys())
    aging_weights = state.get_aging_weights(sensor_ids)

    result = state.interpolator.interpolate_all_metrics(
        x, y, sensors, aging_weights=aging_weights
    )
    result["x"] = x
    result["y"] = y
    return result


@app.get("/stats")
async def get_statistics():
    sensors = list(state.sensors.values())
    if len(sensors) == 0:
        return {"sensors_count": 0, "metrics": {}}

    metrics = {}
    for metric in ["pm25", "co2", "temperature", "humidity"]:
        values = [s[metric] for s in sensors]
        metrics[metric] = {
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
            "values": values,
        }

    return {"sensors_count": len(sensors), "metrics": metrics}


@app.post("/simulator/start")
async def start_simulator(interval: float = 3.0):
    if state.simulator_running:
        return {"status": "already_running"}

    if state.simulator is None:
        from sensor_simulator import SensorSimulator

        state.simulator = SensorSimulator()

    state.simulator_running = True
    state.simulator_task = asyncio.create_task(
        run_simulator_loop(interval)
    )

    return {"status": "started", "interval": interval}


@app.post("/simulator/stop")
async def stop_simulator():
    state.simulator_running = False
    if state.simulator_task:
        state.simulator_task.cancel()
        state.simulator_task = None
    return {"status": "stopped"}


@app.get("/simulator/status")
async def simulator_status():
    return {"running": state.simulator_running}


async def run_simulator_loop(interval: float):
    if state.simulator is None:
        from sensor_simulator import SensorSimulator

        state.simulator = SensorSimulator()

    sensors_cfg = state.simulator.get_default_sensors()
    for sid, scfg in sensors_cfg.items():
        initial = state.simulator.generate_reading(sid)
        reading = SensorReading(**initial)
        state.update_sensor(reading)

    while state.simulator_running:
        try:
            for sid in sensors_cfg:
                raw = state.simulator.generate_reading(sid)
                reading = SensorReading(**raw)
                state.update_sensor(reading)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"模拟器错误: {e}")
            await asyncio.sleep(interval)


@app.post("/mqtt/config")
async def set_mqtt_config(config: MQTTConfig):
    if config.enabled and not state.mqtt_enabled:
        try:
            import paho.mqtt.client as mqtt

            def on_connect(client, userdata, flags, rc):
                if rc == 0:
                    client.subscribe(config.topic)

            def on_message(client, userdata, msg):
                try:
                    payload = json.loads(msg.payload.decode())
                    if isinstance(payload, dict):
                        reading = SensorReading(**payload)
                        state.update_sensor(reading)
                    elif isinstance(payload, list):
                        for item in payload:
                            try:
                                reading = SensorReading(**item)
                                state.update_sensor(reading)
                            except Exception:
                                pass
                except Exception as e:
                    print(f"MQTT消息处理错误: {e}")

            client = mqtt.Client()
            client.on_connect = on_connect
            client.on_message = on_message
            client.connect(config.broker, config.port, 60)
            client.loop_start()

            state.mqtt_client = client
            state.mqtt_enabled = True

        except Exception as e:
            raise HTTPException(status_code=400, detail=f"MQTT连接失败: {str(e)}")

    elif not config.enabled and state.mqtt_enabled:
        if state.mqtt_client:
            state.mqtt_client.loop_stop()
            state.mqtt_client.disconnect()
            state.mqtt_client = None
        state.mqtt_enabled = False

    return {"status": "success", "mqtt_enabled": state.mqtt_enabled}


@app.get("/mqtt/status")
async def mqtt_status():
    return {"enabled": state.mqtt_enabled}


@app.get("/room/config")
async def get_room_config():
    return {
        "width": ROOM_WIDTH,
        "height": ROOM_HEIGHT,
        "unit": "meters",
        "description": "20m x 20m 室内平面",
    }


@app.get("/source/trace")
async def trace_source(
    metric: str = Query(
        default="pm25",
        pattern="^(pm25|co2|temperature|humidity)$",
    ),
):
    sensors = list(state.sensors.values())
    if len(sensors) < 2:
        return {
            "found": False,
            "message": "传感器数量不足，无法溯源",
            "x": ROOM_WIDTH / 2,
            "y": ROOM_HEIGHT / 2,
            "strength": 0,
            "confidence": 0,
            "metric": metric,
        }

    sensor_ids = list(state.sensors.keys())
    weights = state.get_aging_weights(sensor_ids)

    active_sensors = [s for s, w in zip(sensors, weights) if w >= AGING_WEIGHT_THRESHOLD]
    active_weights = [w for w in weights if w >= AGING_WEIGHT_THRESHOLD]

    if len(active_sensors) < 2:
        active_sensors = sensors
        active_weights = None

    result = trace_pollution_source(
        sensors=active_sensors,
        metric=metric,
        sensor_weights=active_weights,
    )
    result["found"] = result["confidence"] > 0.1
    return result


@app.get("/alerts/rules")
async def get_alert_rules():
    return {"rules": list(state.alert_rules.values()), "count": len(state.alert_rules)}


@app.post("/alerts/rules")
async def create_alert_rule(rule: AlertRuleCreate):
    created = state.add_alert_rule(rule)
    return {"status": "success", "rule": created}


@app.put("/alerts/rules/{rule_id}")
async def update_alert_rule(rule_id: str, rule: AlertRuleCreate):
    updated = state.update_alert_rule(rule_id, rule)
    if not updated:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "success", "rule": updated}


@app.delete("/alerts/rules/{rule_id}")
async def delete_alert_rule(rule_id: str):
    if not state.delete_alert_rule(rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "success"}


@app.get("/alerts/events")
async def get_alert_events(limit: int = 50):
    events = state.alert_events[:limit]
    return {"events": events, "count": len(events)}


@app.get("/alerts/check")
async def check_alerts():
    triggered = state.check_alert_rules()
    return {"triggered": triggered, "count": len(triggered)}


@app.post("/alerts/reset")
async def reset_alert_counters():
    state.consecutive_counters.clear()
    state.alert_events.clear()
    return {"status": "success"}


def _seed_default_alert_rules():
    if len(state.alert_rules) > 0:
        return
    defaults = [
        AlertRuleCreate(
            name="PM2.5严重超标",
            metric="pm25",
            operator=">",
            threshold=75.0,
            consecutive_count=3,
            enabled=True,
        ),
        AlertRuleCreate(
            name="CO₂超标警告",
            metric="co2",
            operator=">",
            threshold=1000.0,
            consecutive_count=3,
            enabled=True,
        ),
        AlertRuleCreate(
            name="温度过高",
            metric="temperature",
            operator=">",
            threshold=28.0,
            consecutive_count=3,
            enabled=True,
        ),
        AlertRuleCreate(
            name="湿度过高",
            metric="humidity",
            operator=">",
            threshold=60.0,
            consecutive_count=3,
            enabled=True,
        ),
    ]
    for rule in defaults:
        state.add_alert_rule(rule)


_seed_default_alert_rules()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 启动室内空气质量监测 API 服务")
    print(f"📡 服务地址: http://localhost:{port}")
    print(f"📊 健康检查: http://localhost:{port}/health")
    print(f"📖 API文档: http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
