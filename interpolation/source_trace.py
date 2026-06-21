import numpy as np
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass


ROOM_WIDTH = 20.0
ROOM_HEIGHT = 20.0


@dataclass
class SourceResult:
    x: float
    y: float
    strength: float
    confidence: float
    loss: float
    iterations: int
    converged: bool


def _forward_model(
    source_x: float,
    source_y: float,
    source_strength: float,
    sensor_x: np.ndarray,
    sensor_y: np.ndarray,
    background: float = 0.0,
    decay_power: float = 2.0,
    eps: float = 1e-3,
) -> np.ndarray:
    dx = sensor_x - source_x
    dy = sensor_y - source_y
    distance = np.sqrt(dx ** 2 + dy ** 2)
    denominator = (distance ** decay_power) + eps
    return background + source_strength / denominator


def _compute_loss(
    source_x: float,
    source_y: float,
    source_strength: float,
    sensor_x: np.ndarray,
    sensor_y: np.ndarray,
    sensor_values: np.ndarray,
    weights: Optional[np.ndarray] = None,
    background: float = 0.0,
) -> float:
    predicted = _forward_model(
        source_x, source_y, source_strength, sensor_x, sensor_y, background
    )
    residual = predicted - sensor_values
    if weights is not None:
        return float(np.sum(weights * (residual ** 2)) / np.sum(weights))
    return float(np.mean(residual ** 2))


def _compute_gradients(
    source_x: float,
    source_y: float,
    source_strength: float,
    sensor_x: np.ndarray,
    sensor_y: np.ndarray,
    sensor_values: np.ndarray,
    weights: Optional[np.ndarray] = None,
    background: float = 0.0,
    decay_power: float = 2.0,
    eps: float = 1e-3,
) -> Tuple[float, float, float]:
    dx = sensor_x - source_x
    dy = sensor_y - source_y
    dist_sq = dx ** 2 + dy ** 2
    dist_power = dist_sq ** (decay_power / 2.0)
    denominator = dist_power + eps
    predicted = background + source_strength / denominator
    residual = predicted - sensor_values

    if weights is not None:
        w = weights
        n_sum = np.sum(w)
    else:
        w = np.ones_like(sensor_values)
        n_sum = float(len(sensor_values))

    d_pred_dQ = 1.0 / denominator
    d_pred_dx = source_strength * decay_power * dx * (dist_sq ** (decay_power / 2.0 - 1.0)) / (2.0 * denominator ** 2)
    d_pred_dy = source_strength * decay_power * dy * (dist_sq ** (decay_power / 2.0 - 1.0)) / (2.0 * denominator ** 2)

    grad_Q = float(2.0 * np.sum(w * residual * d_pred_dQ) / n_sum)
    grad_x = float(2.0 * np.sum(w * residual * d_pred_dx) / n_sum)
    grad_y = float(2.0 * np.sum(w * residual * d_pred_dy) / n_sum)

    return grad_x, grad_y, grad_Q


class GradientDescentSourceTracer:
    def __init__(
        self,
        learning_rate: float = 0.05,
        max_iterations: int = 500,
        tolerance: float = 1e-5,
        n_restarts: int = 5,
        decay_power: float = 2.0,
    ):
        self.learning_rate = learning_rate
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.n_restarts = n_restarts
        self.decay_power = decay_power

    def trace_source(
        self,
        sensor_positions: List[Tuple[float, float]],
        sensor_values: List[float],
        sensor_weights: Optional[List[float]] = None,
        room_width: float = ROOM_WIDTH,
        room_height: float = ROOM_HEIGHT,
    ) -> SourceResult:
        if len(sensor_positions) < 2:
            return SourceResult(
                x=room_width / 2.0,
                y=room_height / 2.0,
                strength=0.0,
                confidence=0.0,
                loss=0.0,
                iterations=0,
                converged=False,
            )

        s_x = np.array([p[0] for p in sensor_positions], dtype=np.float64)
        s_y = np.array([p[1] for p in sensor_positions], dtype=np.float64)
        s_vals = np.array(sensor_values, dtype=np.float64)
        w = np.array(sensor_weights, dtype=np.float64) if sensor_weights else None

        if w is not None and len(w) != len(s_x):
            w = None

        mean_val = float(np.mean(s_vals))
        std_val = float(np.std(s_vals))
        if std_val < 1e-3:
            return SourceResult(
                x=float(np.mean(s_x)),
                y=float(np.mean(s_y)),
                strength=mean_val,
                confidence=0.0,
                loss=0.0,
                iterations=0,
                converged=False,
            )

        best_result = None
        best_loss = float("inf")

        start_candidates = self._generate_start_points(
            s_x, s_y, s_vals, room_width, room_height
        )

        for start_x, start_y, start_Q in start_candidates:
            result = self._run_gradient_descent(
                start_x, start_y, start_Q,
                s_x, s_y, s_vals, w,
                mean_val, room_width, room_height,
            )
            if result.loss < best_loss:
                best_loss = result.loss
                best_result = result

        if best_result is None:
            best_result = SourceResult(
                x=room_width / 2.0,
                y=room_height / 2.0,
                strength=mean_val,
                confidence=0.0,
                loss=0.0,
                iterations=0,
                converged=False,
            )

        total_variance = float(np.var(s_vals))
        if total_variance > 1e-6:
            confidence = max(0.0, min(1.0, 1.0 - best_result.loss / total_variance))
        else:
            confidence = 0.0

        best_result.confidence = confidence
        return best_result

    def _generate_start_points(
        self,
        s_x: np.ndarray,
        s_y: np.ndarray,
        s_vals: np.ndarray,
        room_width: float,
        room_height: float,
    ) -> List[Tuple[float, float, float]]:
        candidates = []

        max_idx = int(np.argmax(s_vals))
        candidates.append((
            float(s_x[max_idx]),
            float(s_y[max_idx]),
            float(s_vals[max_idx]) * 5.0,
        ))

        candidates.append((
            float(np.mean(s_x)),
            float(np.mean(s_y)),
            float(np.mean(s_vals)) * 5.0,
        ))

        corners = [
            (0.1 * room_width, 0.1 * room_height),
            (0.9 * room_width, 0.1 * room_height),
            (0.1 * room_width, 0.9 * room_height),
            (0.9 * room_width, 0.9 * room_height),
            (0.5 * room_width, 0.5 * room_height),
        ]
        for cx, cy in corners[: self.n_restarts + 1]:
            candidates.append((cx, cy, float(np.mean(s_vals)) * 10.0))

        return candidates[: max(3, self.n_restarts)]

    def _run_gradient_descent(
        self,
        start_x: float,
        start_y: float,
        start_Q: float,
        s_x: np.ndarray,
        s_y: np.ndarray,
        s_vals: np.ndarray,
        weights: Optional[np.ndarray],
        background: float,
        room_width: float,
        room_height: float,
    ) -> SourceResult:
        x = float(start_x)
        y = float(start_y)
        Q = max(1.0, float(start_Q))

        lr = self.learning_rate
        prev_loss = float("inf")

        for iteration in range(self.max_iterations):
            loss = _compute_loss(x, y, Q, s_x, s_y, s_vals, weights, background)

            if abs(prev_loss - loss) < self.tolerance:
                return SourceResult(
                    x=x, y=y, strength=Q,
                    confidence=0.0, loss=loss,
                    iterations=iteration, converged=True,
                )
            prev_loss = loss

            grad_x, grad_y, grad_Q = _compute_gradients(
                x, y, Q, s_x, s_y, s_vals, weights,
                background, self.decay_power,
            )

            grad_norm = np.sqrt(grad_x ** 2 + grad_y ** 2 + grad_Q ** 2)
            if grad_norm > 1.0:
                grad_x /= grad_norm
                grad_y /= grad_norm
                grad_Q /= grad_norm

            x -= lr * grad_x
            y -= lr * grad_y
            Q -= lr * grad_Q

            x = float(np.clip(x, 0.0, room_width))
            y = float(np.clip(y, 0.0, room_height))
            Q = float(max(0.1, Q))

            if iteration % 50 == 49:
                lr *= 0.9

        final_loss = _compute_loss(x, y, Q, s_x, s_y, s_vals, weights, background)
        return SourceResult(
            x=x, y=y, strength=Q,
            confidence=0.0, loss=final_loss,
            iterations=self.max_iterations, converged=False,
        )


_tracer_instance: Optional[GradientDescentSourceTracer] = None


def get_tracer() -> GradientDescentSourceTracer:
    global _tracer_instance
    if _tracer_instance is None:
        _tracer_instance = GradientDescentSourceTracer()
    return _tracer_instance


def trace_pollution_source(
    sensors: List[Dict],
    metric: str = "pm25",
    sensor_weights: Optional[List[float]] = None,
) -> Dict:
    positions = [(s["x"], s["y"]) for s in sensors]
    values = [s[metric] for s in sensors]

    result = get_tracer().trace_source(
        sensor_positions=positions,
        sensor_values=values,
        sensor_weights=sensor_weights,
    )

    return {
        "x": round(result.x, 3),
        "y": round(result.y, 3),
        "strength": round(result.strength, 3),
        "confidence": round(result.confidence, 3),
        "loss": round(result.loss, 5),
        "iterations": result.iterations,
        "converged": result.converged,
        "metric": metric,
    }
