from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

import httpx

from ai_hub_platform.operations.targets import SloTargets, load_production_targets


class ResilienceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HttpSample:
    elapsed_ms: float
    status_code: int | None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class LoadEvidence:
    status: str
    passed: bool
    requested: int
    completed: int
    scheduled_rps: float
    achieved_rps: float
    wall_seconds: float
    p95_ms: float
    p99_ms: float
    server_errors: int
    server_error_percent: float
    unexpected_statuses: int
    transport_errors: int
    targets: dict[str, float | int]


def percentile(values: Sequence[float], percentage: float) -> float:
    if not values:
        raise ResilienceError("Cannot calculate a percentile without samples")
    if not 0 < percentage <= 100:
        raise ResilienceError("Percentile must be between 0 and 100")
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentage / 100) - 1)
    return ordered[index]


def evaluate_load(
    samples: Sequence[HttpSample],
    *,
    requested: int,
    scheduled_rps: float,
    wall_seconds: float,
    targets: SloTargets,
) -> LoadEvidence:
    if requested <= 0 or scheduled_rps <= 0 or wall_seconds <= 0:
        raise ResilienceError("Load dimensions must be positive")
    durations = [sample.elapsed_ms for sample in samples]
    if not durations:
        raise ResilienceError("Load test did not complete any requests")
    server_errors = sum(
        sample.status_code is not None and 500 <= sample.status_code <= 599
        for sample in samples
    )
    unexpected_statuses = sum(
        sample.status_code is not None and sample.status_code != 200 for sample in samples
    )
    transport_errors = sum(sample.error_type is not None for sample in samples)
    completed = len(samples)
    achieved_rps = completed / wall_seconds
    server_error_percent = server_errors * 100 / completed
    p95_ms = percentile(durations, 95)
    p99_ms = percentile(durations, 99)
    passed = all(
        (
            requested >= targets.minimum_test_requests,
            completed == requested,
            scheduled_rps >= targets.minimum_test_rps,
            achieved_rps >= targets.minimum_test_rps,
            p95_ms <= targets.public_api_p95_ms,
            p99_ms <= targets.public_api_p99_ms,
            server_error_percent <= targets.maximum_server_error_percent,
            unexpected_statuses == 0,
            transport_errors == 0,
        )
    )
    return LoadEvidence(
        status="PASSED" if passed else "FAILED",
        passed=passed,
        requested=requested,
        completed=completed,
        scheduled_rps=round(scheduled_rps, 3),
        achieved_rps=round(achieved_rps, 3),
        wall_seconds=round(wall_seconds, 3),
        p95_ms=round(p95_ms, 3),
        p99_ms=round(p99_ms, 3),
        server_errors=server_errors,
        server_error_percent=round(server_error_percent, 3),
        unexpected_statuses=unexpected_statuses,
        transport_errors=transport_errors,
        targets={
            "minimum_test_requests": targets.minimum_test_requests,
            "minimum_test_rps": targets.minimum_test_rps,
            "public_api_p95_ms": targets.public_api_p95_ms,
            "public_api_p99_ms": targets.public_api_p99_ms,
            "maximum_server_error_percent": targets.maximum_server_error_percent,
        },
    )


async def run_http_load(
    *,
    url: str,
    bearer_token: str,
    requested: int,
    scheduled_rps: float,
    concurrency: int,
    timeout_seconds: float,
    targets: SloTargets,
    transport: httpx.AsyncBaseTransport | None = None,
    headers: Mapping[str, str] | None = None,
) -> LoadEvidence:
    if not bearer_token:
        raise ResilienceError("Load test bearer token is missing")
    if concurrency <= 0 or timeout_seconds <= 0:
        raise ResilienceError("Load concurrency and timeout must be positive")
    semaphore = asyncio.Semaphore(concurrency)
    samples: list[HttpSample] = []
    started = time.perf_counter()
    request_headers = {
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": "ai-hub-m4-load-gate/1",
        **dict(headers or {}),
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        transport=transport,
        trust_env=False,
        headers=request_headers,
    ) as client:

        async def issue(index: int) -> None:
            due_at = started + index / scheduled_rps
            delay = due_at - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            async with semaphore:
                request_started = time.perf_counter()
                try:
                    response = await client.get(url)
                    sample = HttpSample(
                        elapsed_ms=(time.perf_counter() - request_started) * 1000,
                        status_code=response.status_code,
                    )
                except httpx.HTTPError as error:
                    sample = HttpSample(
                        elapsed_ms=(time.perf_counter() - request_started) * 1000,
                        status_code=None,
                        error_type=type(error).__name__,
                    )
                samples.append(sample)

        await asyncio.gather(*(issue(index) for index in range(requested)))
    wall_seconds = time.perf_counter() - started
    return evaluate_load(
        samples,
        requested=requested,
        scheduled_rps=scheduled_rps,
        wall_seconds=wall_seconds,
        targets=targets,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Hub bounded public API load gate")
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--targets", default="deploy/operations/production-targets.json"
    )
    parser.add_argument("--requests", type=int)
    parser.add_argument("--rps", type=float)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--token-environment", default="AI_HUB_LOAD_BEARER_TOKEN")
    return parser


def run_command(args: argparse.Namespace) -> LoadEvidence:
    targets = load_production_targets(str(args.targets)).slo
    requested = int(args.requests or targets.minimum_test_requests)
    scheduled_rps = float(args.rps or targets.minimum_test_rps * 1.25)
    token = os.environ.get(str(args.token_environment), "")
    return asyncio.run(
        run_http_load(
            url=str(args.url),
            bearer_token=token,
            requested=requested,
            scheduled_rps=scheduled_rps,
            concurrency=int(args.concurrency),
            timeout_seconds=float(args.timeout_seconds),
            targets=targets,
        )
    )


def main() -> None:
    try:
        evidence = run_command(build_parser().parse_args())
    except ResilienceError as error:
        print(json.dumps({"status": "FAILED", "passed": False, "error": str(error)}))
        raise SystemExit(1) from error
    print(json.dumps(asdict(evidence), ensure_ascii=False, sort_keys=True))
    if not evidence.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
