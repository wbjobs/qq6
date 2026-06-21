import numpy as np
from pykrige.ok import OrdinaryKriging
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timezone


ROOM_WIDTH = 20.0
ROOM_HEIGHT = 20.0

AGING_GRACE_PERIOD = 300.0
AGING_DECAY_RATE = 0.02
AGING_WEIGHT_THRESHOLD = 0.1


def compute_aging_weights(
    timestamps: List[Optional[str]],
    grace_period: float = AGING_GRACE_PERIOD,
    decay_rate: float = AGING_DECAY_RATE,
    weight_threshold: float = AGING_WEIGHT_THRESHOLD,
) -> List[float]:
    now = datetime.now(timezone.utc).timestamp()
    weights = []

    for ts in timestamps:
        if ts is None:
            weights.append(0.0)
            continue

        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_seconds = now - dt.timestamp()
        except (ValueError, TypeError):
            weights.append(0.0)
            continue

        if age_seconds <= grace_period:
            weights.append(1.0)
        else:
            excess_age = age_seconds - grace_period
            w = float(np.exp(-decay_rate * excess_age))
            weights.append(w)

    return weights


def apply_aging_to_values(
    values: List[float],
    weights: List[float],
) -> List[float]:
    active_indices = [i for i, w in enumerate(weights) if w >= AGING_WEIGHT_THRESHOLD]
    if not active_indices:
        return values

    active_values = [values[i] for i in active_indices]
    active_weights = [weights[i] for i in active_indices]

    weighted_sum = sum(v * w for v, w in zip(active_values, active_weights))
    total_weight = sum(active_weights)
    weighted_mean = weighted_sum / total_weight if total_weight > 0 else np.mean(active_values)

    adjusted = []
    for v, w in zip(values, weights):
        adjusted_val = w * v + (1.0 - w) * weighted_mean
        adjusted.append(adjusted_val)

    return adjusted


def filter_by_aging_weight(
    positions: List[Tuple[float, float]],
    values: List[float],
    weights: List[float],
    threshold: float = AGING_WEIGHT_THRESHOLD,
) -> Tuple[List[Tuple[float, float]], List[float], List[float]]:
    filtered_pos = []
    filtered_val = []
    filtered_w = []

    for pos, val, w in zip(positions, values, weights):
        if w >= threshold:
            filtered_pos.append(pos)
            filtered_val.append(val)
            filtered_w.append(w)

    return filtered_pos, filtered_val, filtered_w


class KrigingInterpolator:
    def __init__(self, nlags: int = 6, variogram_model: str = "spherical"):
        self.nlags = nlags
        self.variogram_model = variogram_model
        self._last_grid_cache = {}

    def interpolate(
        self,
        sensor_positions: List[Tuple[float, float]],
        sensor_values: List[float],
        resolution: int = 40,
        room_width: float = ROOM_WIDTH,
        room_height: float = ROOM_HEIGHT,
        aging_weights: Optional[List[float]] = None,
    ) -> Dict:
        if aging_weights is not None:
            filtered_pos, filtered_vals, filtered_w = filter_by_aging_weight(
                sensor_positions, sensor_values, aging_weights
            )
            adjusted_vals = apply_aging_to_values(filtered_vals, filtered_w)
        else:
            filtered_pos = sensor_positions
            adjusted_vals = sensor_values

        if len(filtered_pos) < 3:
            return self._fallback_interpolation(
                filtered_pos, adjusted_vals, resolution, room_width, room_height
            )

        x_coords = np.array([p[0] for p in filtered_pos], dtype=np.float64)
        y_coords = np.array([p[1] for p in filtered_pos], dtype=np.float64)
        values = np.array(adjusted_vals, dtype=np.float64)

        if np.std(values) < 1e-10:
            grid_val = float(np.mean(values))
            grid_x = np.linspace(0, room_width, resolution)
            grid_y = np.linspace(0, room_height, resolution)
            z_grid = np.full((resolution, resolution), grid_val, dtype=np.float64)
            return {
                "grid": z_grid.tolist(),
                "variance": [],
                "min_val": float(np.min(z_grid)),
                "max_val": float(np.max(z_grid)),
                "mean_val": float(np.mean(z_grid)),
                "grid_x": grid_x.tolist(),
                "grid_y": grid_y.tolist(),
            }

        grid_x = np.linspace(0, room_width, resolution)
        grid_y = np.linspace(0, room_height, resolution)

        try:
            OK = OrdinaryKriging(
                x_coords,
                y_coords,
                values,
                variogram_model=self.variogram_model,
                nlags=self.nlags,
                verbose=False,
                enable_plotting=False,
                exact_values=True,
            )

            z_grid, sigma_grid = OK.execute("grid", grid_x, grid_y)

            z_grid = np.nan_to_num(z_grid, nan=np.nanmean(values))

            return {
                "grid": z_grid.tolist(),
                "variance": sigma_grid.tolist() if sigma_grid is not None else [],
                "min_val": float(np.min(z_grid)),
                "max_val": float(np.max(z_grid)),
                "mean_val": float(np.mean(z_grid)),
                "grid_x": grid_x.tolist(),
                "grid_y": grid_y.tolist(),
            }

        except Exception as e:
            print(f"克里金插值失败，使用回退方法: {e}")
            return self._fallback_interpolation(
                filtered_pos, adjusted_vals, resolution, room_width, room_height
            )

    def _fallback_interpolation(
        self,
        sensor_positions: List[Tuple[float, float]],
        sensor_values: List[float],
        resolution: int,
        room_width: float,
        room_height: float,
    ) -> Dict:
        grid_x = np.linspace(0, room_width, resolution)
        grid_y = np.linspace(0, room_height, resolution)
        grid = np.zeros((resolution, resolution), dtype=np.float64)

        if len(sensor_positions) == 0:
            return {
                "grid": grid.tolist(),
                "variance": [],
                "min_val": 0.0,
                "max_val": 0.0,
                "mean_val": 0.0,
                "grid_x": grid_x.tolist(),
                "grid_y": grid_y.tolist(),
            }

        for i, gy in enumerate(grid_y):
            for j, gx in enumerate(grid_x):
                distances = []
                weights = []
                values_list = []

                for k, (sx, sy) in enumerate(sensor_positions):
                    dist = np.sqrt((gx - sx) ** 2 + (gy - sy) ** 2)
                    if dist < 1e-6:
                        grid[i, j] = sensor_values[k]
                        break
                    distances.append(dist)
                    weights.append(1.0 / (dist ** 2 + 1e-6))
                    values_list.append(sensor_values[k])
                else:
                    total_weight = sum(weights)
                    if total_weight > 0:
                        grid[i, j] = sum(
                            w * v for w, v in zip(weights, values_list)
                        ) / total_weight
                    else:
                        grid[i, j] = np.mean(values_list) if values_list else 0.0

        return {
            "grid": grid.tolist(),
            "variance": [],
            "min_val": float(np.min(grid)),
            "max_val": float(np.max(grid)),
            "mean_val": float(np.mean(grid)),
            "grid_x": grid_x.tolist(),
            "grid_y": grid_y.tolist(),
        }

    def interpolate_point(
        self,
        x: float,
        y: float,
        sensor_positions: List[Tuple[float, float]],
        sensor_values: List[float],
        aging_weights: Optional[List[float]] = None,
    ) -> float:
        if aging_weights is not None:
            filtered_pos, filtered_vals, filtered_w = filter_by_aging_weight(
                sensor_positions, sensor_values, aging_weights
            )
            adjusted_vals = apply_aging_to_values(filtered_vals, filtered_w)
        else:
            filtered_pos = sensor_positions
            adjusted_vals = sensor_values

        if len(filtered_pos) == 0:
            return 0.0

        x_arr = np.array([x], dtype=np.float64)
        y_arr = np.array([y], dtype=np.float64)
        s_x = np.array([p[0] for p in filtered_pos], dtype=np.float64)
        s_y = np.array([p[1] for p in filtered_pos], dtype=np.float64)
        vals = np.array(adjusted_vals, dtype=np.float64)

        for i, (sx, sy) in enumerate(filtered_pos):
            if abs(x - sx) < 1e-3 and abs(y - sy) < 1e-3:
                return float(vals[i])

        try:
            if len(filtered_pos) >= 3:
                OK = OrdinaryKriging(
                    s_x,
                    s_y,
                    vals,
                    variogram_model=self.variogram_model,
                    nlags=self.nlags,
                    verbose=False,
                    enable_plotting=False,
                )
                z, _ = OK.execute("grid", x_arr, y_arr)
                return float(z[0, 0])
        except Exception:
            pass

        distances = np.sqrt((s_x - x) ** 2 + (s_y - y) ** 2)
        weights = 1.0 / (distances ** 2 + 1e-6)
        total_weight = np.sum(weights)

        if total_weight > 0:
            return float(np.sum(weights * vals) / total_weight)
        return float(np.mean(vals))

    def interpolate_all_metrics(
        self,
        x: float,
        y: float,
        sensors: List[Dict],
        aging_weights: Optional[List[float]] = None,
    ) -> Dict[str, float]:
        if aging_weights is not None:
            filtered_indices = [
                i for i, w in enumerate(aging_weights) if w >= AGING_WEIGHT_THRESHOLD
            ]
            filtered_w = [aging_weights[i] for i in filtered_indices]
            filtered_sensors = [sensors[i] for i in filtered_indices]
        else:
            filtered_sensors = sensors
            filtered_w = None

        positions = [(s["x"], s["y"]) for s in filtered_sensors]

        result = {}
        for metric in ["pm25", "co2", "temperature", "humidity"]:
            values = [s[metric] for s in filtered_sensors]
            if filtered_w is not None:
                adjusted_vals = apply_aging_to_values(values, filtered_w)
            else:
                adjusted_vals = values
            result[metric] = self.interpolate_point(
                x, y, positions, adjusted_vals, aging_weights=None
            )

        return result
