import random
import math
import json
import csv
import time
from datetime import datetime
from typing import Dict, List, Tuple
from dataclasses import dataclass, field


@dataclass
class SensorConfig:
    id: str
    name: str
    x: float
    y: float
    base_pm25: float = 35.0
    base_co2: float = 600.0
    base_temperature: float = 23.0
    base_humidity: float = 45.0
    noise_level: Dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.noise_level:
            self.noise_level = {
                "pm25": 8.0,
                "co2": 80.0,
                "temperature": 0.8,
                "humidity": 3.0,
            }


class SensorSimulator:
    def __init__(self, seed: int = None):
        if seed is not None:
            random.seed(seed)
        self.sensors: Dict[str, SensorConfig] = self.get_default_sensors()
        self._trend_params: Dict[str, Dict] = {}
        self._init_trends()
        self._step = 0

    def get_default_sensors(self) -> Dict[str, SensorConfig]:
        default_sensors = [
            SensorConfig(
                id="sensor_001",
                name="S1-入口",
                x=2.0,
                y=2.0,
                base_pm25=45.0,
                base_co2=700.0,
                base_temperature=24.0,
                base_humidity=40.0,
            ),
            SensorConfig(
                id="sensor_002",
                name="S2-办公区A",
                x=5.0,
                y=15.0,
                base_pm25=30.0,
                base_co2=550.0,
                base_temperature=22.5,
                base_humidity=48.0,
            ),
            SensorConfig(
                id="sensor_003",
                name="S3-办公区B",
                x=10.0,
                y=10.0,
                base_pm25=38.0,
                base_co2=650.0,
                base_temperature=23.5,
                base_humidity=45.0,
            ),
            SensorConfig(
                id="sensor_004",
                name="S4-会议室",
                x=15.0,
                y=16.0,
                base_pm25=25.0,
                base_co2=500.0,
                base_temperature=22.0,
                base_humidity=50.0,
            ),
            SensorConfig(
                id="sensor_005",
                name="S5-休息区",
                x=17.0,
                y=4.0,
                base_pm25=50.0,
                base_co2=800.0,
                base_temperature=24.5,
                base_humidity=42.0,
            ),
            SensorConfig(
                id="sensor_006",
                name="S6-中心",
                x=10.0,
                y=18.0,
                base_pm25=32.0,
                base_co2=580.0,
                base_temperature=23.0,
                base_humidity=46.0,
            ),
            SensorConfig(
                id="sensor_007",
                name="S7-西北角",
                x=3.0,
                y=18.0,
                base_pm25=28.0,
                base_co2=520.0,
                base_temperature=21.5,
                base_humidity=52.0,
            ),
            SensorConfig(
                id="sensor_008",
                name="S8-东南角",
                x=18.0,
                y=2.0,
                base_pm25=55.0,
                base_co2=850.0,
                base_temperature=25.0,
                base_humidity=38.0,
            ),
        ]
        return {s.id: s for s in default_sensors}

    def _init_trends(self):
        for sid in self.sensors:
            self._trend_params[sid] = {
                "pm25_phase": random.uniform(0, 2 * math.pi),
                "co2_phase": random.uniform(0, 2 * math.pi),
                "temp_phase": random.uniform(0, 2 * math.pi),
                "hum_phase": random.uniform(0, 2 * math.pi),
                "pm25_freq": random.uniform(0.02, 0.08),
                "co2_freq": random.uniform(0.03, 0.09),
                "temp_freq": random.uniform(0.01, 0.04),
                "hum_freq": random.uniform(0.015, 0.05),
            }

    def generate_reading(self, sensor_id: str) -> Dict:
        if sensor_id not in self.sensors:
            raise ValueError(f"传感器 {sensor_id} 不存在")

        cfg = self.sensors[sensor_id]
        tp = self._trend_params[sensor_id]
        self._step += 1
        step = self._step

        pm25 = self._compute_value(
            base=cfg.base_pm25,
            noise=cfg.noise_level["pm25"],
            phase=tp["pm25_phase"],
            freq=tp["pm25_freq"],
            step=step,
            amplitude_scale=0.3,
            min_val=5.0,
            max_val=200.0,
            spike_prob=0.02,
            spike_magnitude=30.0,
        )

        co2 = self._compute_value(
            base=cfg.base_co2,
            noise=cfg.noise_level["co2"],
            phase=tp["co2_phase"],
            freq=tp["co2_freq"],
            step=step,
            amplitude_scale=0.25,
            min_val=400.0,
            max_val=2500.0,
            spike_prob=0.015,
            spike_magnitude=200.0,
        )

        temperature = self._compute_value(
            base=cfg.base_temperature,
            noise=cfg.noise_level["temperature"],
            phase=tp["temp_phase"],
            freq=tp["temp_freq"],
            step=step,
            amplitude_scale=0.4,
            min_val=15.0,
            max_val=35.0,
            spike_prob=0.0,
            spike_magnitude=0.0,
        )

        humidity = self._compute_value(
            base=cfg.base_humidity,
            noise=cfg.noise_level["humidity"],
            phase=tp["hum_phase"],
            freq=tp["hum_freq"],
            step=step,
            amplitude_scale=0.3,
            min_val=20.0,
            max_val=80.0,
            spike_prob=0.01,
            spike_magnitude=8.0,
        )

        return {
            "id": sensor_id,
            "name": cfg.name,
            "x": cfg.x,
            "y": cfg.y,
            "pm25": round(pm25, 2),
            "co2": round(co2, 2),
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "timestamp": datetime.now().isoformat(),
        }

    def _compute_value(
        self,
        base: float,
        noise: float,
        phase: float,
        freq: float,
        step: int,
        amplitude_scale: float = 0.3,
        min_val: float = 0.0,
        max_val: float = 1000.0,
        spike_prob: float = 0.0,
        spike_magnitude: float = 0.0,
    ) -> float:
        trend_amplitude = base * amplitude_scale
        trend = trend_amplitude * math.sin(freq * step + phase)

        gaussian_noise = random.gauss(0, noise)

        spike = 0.0
        if random.random() < spike_prob:
            direction = 1 if random.random() < 0.7 else -1
            spike = direction * spike_magnitude * random.uniform(0.5, 1.0)

        value = base + trend + gaussian_noise + spike
        value = max(min_val, min(max_val, value))

        return value

    def generate_all_readings(self) -> List[Dict]:
        return [self.generate_reading(sid) for sid in self.sensors]

    def add_sensor(self, config: SensorConfig) -> None:
        self.sensors[config.id] = config
        self._trend_params[config.id] = {
            "pm25_phase": random.uniform(0, 2 * math.pi),
            "co2_phase": random.uniform(0, 2 * math.pi),
            "temp_phase": random.uniform(0, 2 * math.pi),
            "hum_phase": random.uniform(0, 2 * math.pi),
            "pm25_freq": random.uniform(0.02, 0.08),
            "co2_freq": random.uniform(0.03, 0.09),
            "temp_freq": random.uniform(0.01, 0.04),
            "hum_freq": random.uniform(0.015, 0.05),
        }

    def remove_sensor(self, sensor_id: str) -> bool:
        if sensor_id in self.sensors:
            del self.sensors[sensor_id]
            if sensor_id in self._trend_params:
                del self._trend_params[sensor_id]
            return True
        return False

    def export_to_csv(self, filename: str, num_records: int = 100, interval: float = 1.0) -> None:
        fieldnames = [
            "id", "name", "x", "y", "pm25", "co2", "temperature", "humidity", "timestamp"
        ]

        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for i in range(num_records):
                readings = self.generate_all_readings()
                for reading in readings:
                    writer.writerow(reading)
                if interval > 0 and i < num_records - 1:
                    self._step += 1

        print(f"✅ 已导出 {num_records} 条记录到 {filename}")

    def export_to_json(self, filename: str, num_records: int = 100) -> None:
        all_data = []
        for i in range(num_records):
            batch = self.generate_all_readings()
            all_data.append({
                "batch_id": i,
                "timestamp": datetime.now().isoformat(),
                "readings": batch,
            })
            self._step += 1

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)

        print(f"✅ 已导出 {num_records} 批记录到 {filename}")

    def simulate_stream(self, interval: float = 3.0, callback=None, duration: float = None) -> None:
        start_time = time.time()
        print(f"🎯 开始模拟传感器数据流 (间隔: {interval}s)...")
        if duration:
            print(f"⏱️  模拟时长: {duration}s")

        try:
            while True:
                if duration and (time.time() - start_time) >= duration:
                    print(f"✅ 模拟完成，已运行 {duration}s")
                    break

                readings = self.generate_all_readings()
                batch_time = datetime.now().strftime("%H:%M:%S")

                if callback:
                    callback(readings)
                else:
                    pm25_values = [r["pm25"] for r in readings]
                    co2_values = [r["co2"] for r in readings]
                    print(
                        f"[{batch_time}] "
                        f"PM2.5: {min(pm25_values):.1f}~{max(pm25_values):.1f} μg/m³ | "
                        f"CO2: {min(co2_values):.0f}~{max(co2_values):.0f} ppm"
                    )

                time.sleep(interval)

        except KeyboardInterrupt:
            print("\n⏹️  模拟已停止")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="传感器数据模拟器")
    parser.add_argument("--mode", choices=["csv", "json", "stream"], default="stream",
                        help="运行模式: csv(导出CSV), json(导出JSON), stream(实时流)")
    parser.add_argument("--records", type=int, default=100,
                        help="导出记录数量 (csv/json模式)")
    parser.add_argument("--output", default="sensor_data",
                        help="输出文件名前缀")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="数据流间隔秒数 (stream模式)")
    parser.add_argument("--duration", type=float, default=None,
                        help="模拟持续时间秒数 (stream模式)")
    parser.add_argument("--seed", type=int, default=None,
                        help="随机种子，用于复现实验")

    args = parser.parse_args()

    simulator = SensorSimulator(seed=args.seed)

    print("=" * 60)
    print("📡 室内空气质量传感器模拟器")
    print("=" * 60)
    print(f"传感器数量: {len(simulator.sensors)}")
    for sid, cfg in simulator.sensors.items():
        print(f"  - {cfg.name} ({sid}) 位置: ({cfg.x}m, {cfg.y}m)")
    print("=" * 60)

    if args.mode == "csv":
        filename = f"{args.output}.csv"
        simulator.export_to_csv(filename, num_records=args.records)
    elif args.mode == "json":
        filename = f"{args.output}.json"
        simulator.export_to_json(filename, num_records=args.records)
    else:
        simulator.simulate_stream(
            interval=args.interval,
            duration=args.duration,
        )
