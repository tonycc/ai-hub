from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

HISTOGRAM_BUCKETS_SECONDS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    1.5,
    2.5,
    5.0,
    10.0,
)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if math.isfinite(value):
        return format(value, ".12g")
    return "+Inf" if value > 0 else "-Inf"


@dataclass(slots=True)
class HistogramValue:
    count: int
    total_seconds: float
    buckets: list[int]


class MetricsRegistry:
    """Small bounded-cardinality OpenMetrics registry for the single API process."""

    def __init__(self, *, service: str, version: str) -> None:
        self._service = service
        self._version = version
        self._started_at = time.time()
        self._lock = threading.Lock()
        self._in_flight = 0
        self._request_counts: defaultdict[tuple[str, str, str], int] = defaultdict(int)
        self._durations: dict[tuple[str, str], HistogramValue] = {}

    def request_started(self) -> None:
        with self._lock:
            self._in_flight += 1

    def request_finished(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        status_class = f"{status_code // 100}xx" if 100 <= status_code <= 599 else "unknown"
        key = (method, route)
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._request_counts[(method, route, status_class)] += 1
            histogram = self._durations.get(key)
            if histogram is None:
                histogram = HistogramValue(
                    count=0,
                    total_seconds=0.0,
                    buckets=[0] * len(HISTOGRAM_BUCKETS_SECONDS),
                )
                self._durations[key] = histogram
            histogram.count += 1
            histogram.total_seconds += duration_seconds
            for index, upper_bound in enumerate(HISTOGRAM_BUCKETS_SECONDS):
                if duration_seconds <= upper_bound:
                    histogram.buckets[index] += 1

    def render(self) -> str:
        with self._lock:
            in_flight = self._in_flight
            request_counts = dict(self._request_counts)
            durations = {
                key: HistogramValue(value.count, value.total_seconds, value.buckets.copy())
                for key, value in self._durations.items()
            }
        service = _escape_label(self._service)
        version = _escape_label(self._version)
        lines = [
            "# HELP ai_hub_build_info AI Hub service build information.",
            "# TYPE ai_hub_build_info gauge",
            f'ai_hub_build_info{{service="{service}",version="{version}"}} 1',
            "# HELP ai_hub_process_start_time_seconds Process start time since Unix epoch.",
            "# TYPE ai_hub_process_start_time_seconds gauge",
            f"ai_hub_process_start_time_seconds {_format_number(self._started_at)}",
            "# HELP ai_hub_http_in_flight_requests HTTP requests currently executing.",
            "# TYPE ai_hub_http_in_flight_requests gauge",
            f"ai_hub_http_in_flight_requests {in_flight}",
            "# HELP ai_hub_http_requests_total Completed HTTP requests.",
            "# TYPE ai_hub_http_requests_total counter",
        ]
        for (method, route, status_class), count in sorted(request_counts.items()):
            labels = (
                f'method="{_escape_label(method)}",route="{_escape_label(route)}",'
                f'status_class="{_escape_label(status_class)}"'
            )
            lines.append(f"ai_hub_http_requests_total{{{labels}}} {count}")
        lines.extend(
            [
                "# HELP ai_hub_http_request_duration_seconds HTTP request duration.",
                "# TYPE ai_hub_http_request_duration_seconds histogram",
            ]
        )
        for (method, route), value in sorted(durations.items()):
            labels = f'method="{_escape_label(method)}",route="{_escape_label(route)}"'
            for upper_bound, count in zip(
                HISTOGRAM_BUCKETS_SECONDS, value.buckets, strict=True
            ):
                lines.append(
                    "ai_hub_http_request_duration_seconds_bucket"
                    f'{{{labels},le="{_format_number(upper_bound)}"}} {count}'
                )
            lines.append(
                "ai_hub_http_request_duration_seconds_bucket"
                f'{{{labels},le="+Inf"}} {value.count}'
            )
            lines.append(
                f"ai_hub_http_request_duration_seconds_sum{{{labels}}} "
                f"{_format_number(value.total_seconds)}"
            )
            lines.append(
                f"ai_hub_http_request_duration_seconds_count{{{labels}}} {value.count}"
            )
        lines.append("# EOF")
        return "\n".join(lines) + "\n"


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, registry: MetricsRegistry) -> None:
        super().__init__(app)
        self._registry = registry

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/internal/metrics":
            return await call_next(request)
        self._registry.request_started()
        started_at = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route_value = request.scope.get("route")
            route_path = getattr(route_value, "path", None)
            route = route_path if isinstance(route_path, str) else "__unmatched__"
            self._registry.request_finished(
                method=request.method,
                route=route,
                status_code=status_code,
                duration_seconds=max(0.0, time.monotonic() - started_at),
            )
