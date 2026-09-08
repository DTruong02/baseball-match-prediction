#!/usr/bin/env python3
"""
Light load test for HTTP API endpoints and live WebSocket feeds (Stage 7.4).

Examples:
  python scripts/load_test.py --base-url http://127.0.0.1:8000
  python scripts/load_test.py --base-url http://127.0.0.1:8000 --ws-game-pk 746874 \\
      --concurrency 20 --requests 200
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from typing import Any

import httpx

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]


async def _http_worker(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore,
    results: list[float],
    errors: list[str],
) -> None:
    async with sem:
        start = time.perf_counter()
        try:
            response = await client.get(url)
            elapsed = time.perf_counter() - start
            if response.status_code >= 400:
                errors.append(f"{url} -> {response.status_code}")
            else:
                results.append(elapsed)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url} -> {exc}")


async def run_http_load(
    *,
    base_url: str,
    paths: list[str],
    concurrency: int,
    requests: int,
) -> dict[str, Any]:
    sem = asyncio.Semaphore(concurrency)
    results: list[float] = []
    errors: list[str] = []
    urls = [f"{base_url.rstrip('/')}{path}" for path in paths]

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = []
        for i in range(requests):
            url = urls[i % len(urls)]
            tasks.append(_http_worker(client, url, sem, results, errors))
        started = time.perf_counter()
        await asyncio.gather(*tasks)
        wall = time.perf_counter() - started

    summary: dict[str, Any] = {
        "requests": requests,
        "ok": len(results),
        "errors": len(errors),
        "wall_seconds": round(wall, 3),
        "rps": round(len(results) / wall, 2) if wall > 0 else 0.0,
    }
    if results:
        summary["latency_ms"] = {
            "p50": round(statistics.median(results) * 1000, 2),
            "p95": round(_percentile(results, 0.95) * 1000, 2),
            "max": round(max(results) * 1000, 2),
        }
    if errors:
        summary["error_samples"] = errors[:5]
    return summary


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


async def run_ws_load(
    *,
    base_url: str,
    game_pk: int,
    clients: int,
    duration_seconds: float,
) -> dict[str, Any]:
    if websockets is None:
        return {"skipped": True, "reason": "websockets package not installed"}

    http_base = base_url.rstrip("/")
    if http_base.startswith("https://"):
        ws_base = "wss://" + http_base[len("https://") :]
    elif http_base.startswith("http://"):
        ws_base = "ws://" + http_base[len("http://") :]
    else:
        ws_base = http_base
    url = f"{ws_base}/ws/games/{game_pk}"

    connected = 0
    messages = 0
    errors: list[str] = []

    async def one_client(client_id: int) -> None:
        nonlocal connected, messages
        try:
            async with websockets.connect(url, open_timeout=10) as ws:
                connected += 1
                deadline = time.perf_counter() + duration_seconds
                while time.perf_counter() < deadline:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=1.0)
                        messages += 1
                    except asyncio.TimeoutError:
                        continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"client-{client_id}: {exc}")

    started = time.perf_counter()
    await asyncio.gather(*(one_client(i) for i in range(clients)))
    wall = time.perf_counter() - started
    return {
        "url": url,
        "clients": clients,
        "connected": connected,
        "messages_received": messages,
        "errors": len(errors),
        "error_samples": errors[:5],
        "wall_seconds": round(wall, 3),
    }


async def async_main(args: argparse.Namespace) -> int:
    http_summary = await run_http_load(
        base_url=args.base_url,
        paths=args.path,
        concurrency=args.concurrency,
        requests=args.requests,
    )
    print("HTTP load")
    for key, value in http_summary.items():
        print(f"  {key}: {value}")

    if args.ws_game_pk is not None:
        ws_summary = await run_ws_load(
            base_url=args.base_url,
            game_pk=args.ws_game_pk,
            clients=args.ws_clients,
            duration_seconds=args.ws_duration,
        )
        print("WebSocket load")
        for key, value in ws_summary.items():
            print(f"  {key}: {value}")

    # Non-zero exit if HTTP error rate is high.
    if http_summary.get("errors", 0) > http_summary.get("ok", 0) * 0.1:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Light API + WebSocket load test")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="API base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="HTTP path to hit (repeatable). Defaults to /health, /ready, /games",
    )
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument(
        "--ws-game-pk",
        type=int,
        default=None,
        help="If set, also open WebSocket clients to /ws/games/{pk}",
    )
    parser.add_argument("--ws-clients", type=int, default=5)
    parser.add_argument("--ws-duration", type=float, default=5.0)
    args = parser.parse_args()
    if not args.path:
        args.path = ["/health", "/ready", "/games"]
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
