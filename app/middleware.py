"""HTTP 中间件：请求关联 ID、访问日志、安全响应头。

纯 ASGI 实现（不使用 ``BaseHTTPMiddleware``）：
- 与业务处理跑在同一个 task 里，``contextvars`` 可靠向下传递，日志自动带 request_id；
- 只包装 ``send``，不缓冲响应体，产物下载（WAV / MP4）不会被整段读进内存。

顺序（由 ``create_app`` 决定，最后添加的在最外层）::

    CORS → RequestContext → SecurityHeaders → 路由
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.logging import get_logger, request_id_var

log = get_logger("app.http")

# 基础安全响应头。控制台是同源单页应用，不需要放宽任何一项。
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
)


class RequestContextMiddleware:
    """为每个请求分配关联 ID，输出一条访问日志，并把 ID 回写到响应头。"""

    def __init__(self, app: Any, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self._header_key = header_name.lower().encode("latin-1")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or ())
        raw_id = incoming.get(self._header_key, b"").decode("latin-1").strip()
        request_id = raw_id[:128] if raw_id else uuid.uuid4().hex

        token = request_id_var.set(request_id)
        scope = dict(scope)
        scope["state"] = {**(scope.get("state") or {}), "request_id": request_id}

        started = time.perf_counter()
        status_code = 500

        async def send_with_context(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != self._header_key]
                headers.append((self._header_key, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception:
            log.exception(
                "request_failed",
                extra={"method": scope.get("method"), "path": scope.get("path"), "duration_ms": _elapsed_ms(started)},
            )
            raise
        finally:
            request_id_var.reset(token)
            log.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": _elapsed_ms(started),
                    "request_id": request_id,
                },
            )


class SecurityHeadersMiddleware:
    """给所有响应补齐基础安全头，避免个别端点漏配。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                existing = {key.lower() for key, _ in message.get("headers", [])}
                headers = list(message.get("headers", []))
                headers.extend((key, value) for key, value in SECURITY_HEADERS if key not in existing)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
