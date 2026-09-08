"""ASGI middleware: request latency, error counters, and structured access logs."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from baseball_backend.metrics import observe_request

logger = logging.getLogger("baseball_backend.access")


class RequestMetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        request_id = str(uuid.uuid4())
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration = time.perf_counter() - start
            observe_request(
                method=method, path=path, status_code=500, duration=duration
            )
            logger.exception(
                "Unhandled error",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            raise

        duration = time.perf_counter() - start
        observe_request(
            method=method, path=path, status_code=status_code, duration=duration
        )
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": round(duration * 1000, 2),
            },
        )
