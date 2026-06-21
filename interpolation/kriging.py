import numpy as np
from pykrige.ok import OrdinaryKriging
from typing import List, Dict, Tuple, Optional


ROOM_WIDTH = 20.0
ROOM_HEIGHT = 20.0


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
    ) -> Dict:
        if len(sensor_positions) < 3:
            return self._fallback_interpolation(
                sensor_positions, sensor_values, resolution, room_width, room_height
            )

        x_coords = np.array([p[0] for p in sensor_positions], dtype=np.float64)
        y_coords = np.array([p[1] for p in sensor_positions], dtype=np.float64)
        values = np.array(sensor_values, dtype=np.float64)

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
                sensor_positions, sensor_values, resolution, room_width, room_height
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
    ) -> float:
        if len(sensor_positions) == 0:
            return 0.0

        x_arr = np.array([x], dtype=np.float64)
        y_arr = np.array([y], dtype=np.float64)
        s_x = np.array([p[0] for p in sensor_positions], dtype=np.float64)
        s_y = np.array([p[1] for p in sensor_positions], dtype=np.float64)
        vals = np.array(sensor_values, dtype=np.float64)

        for i, (sx, sy) in enumerate(sensor_positions):
            if abs(x - sx) < 1e-3 and abs(y - sy) < 1e-3:
                return float(vals[i])

        try:
            if len(sensor_positions) >= 3:
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
        except Exception as e:
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
    ) -> Dict[str, float]:
        positions = [(s["x"], s["y"]) for s in sensors]

        result = {}
        for metric in ["pm25", "co2", "temperature", "humidity"]:
            values = [s[metric] for s in sensors]
            result[metric] = self.interpolate_point(x, y, positions, values)

        return result
