"""Dependency-free HTTP security controls for the operator API."""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_INLINE_STYLE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)
_DOCUMENTATION_PATHS = {"/docs", "/redoc", "/openapi.json"}


def _sha256_source(content: str) -> str:
    digest = base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()
    return f"'sha256-{digest}'"


def operator_content_security_policy(document: Path) -> str:
    html = document.read_text()
    styles = _INLINE_STYLE.findall(html)
    scripts = _INLINE_SCRIPT.findall(html)
    if len(styles) != 1 or len(scripts) != 1:
        raise ValueError("Operator document must contain exactly one inline style and script")
    return "; ".join(
        (
            "default-src 'none'",
            f"script-src {_sha256_source(scripts[0])}",
            f"style-src {_sha256_source(styles[0])}",
            "connect-src 'self'",
            "img-src 'self' data:",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        )
    )


class SecurityHeadersMiddleware:
    """Attach a restrictive policy to every response without changing response bodies."""

    def __init__(self, app: ASGIApp, operator_document: Path) -> None:
        self.app = app
        self.operator_policy = operator_content_security_policy(operator_document)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        documentation_hidden = (
            path in _DOCUMENTATION_PATHS
            and os.getenv("API_AUTH_REQUIRED", "false").lower() != "false"
        )

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
                )
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["X-Permitted-Cross-Domain-Policies"] = "none"
                headers["Cache-Control"] = "no-store"
                if path == "/operator":
                    headers["Content-Security-Policy"] = self.operator_policy
                elif path not in _DOCUMENTATION_PATHS or documentation_hidden:
                    headers["Content-Security-Policy"] = (
                        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                    )
            await send(message)

        if documentation_hidden:
            await JSONResponse({"detail": "Not found"}, status_code=404)(
                scope, receive, secure_send
            )
            return

        await self.app(scope, receive, secure_send)


class RequestSizeLimitMiddleware:
    """Reject ambiguous, encoded, or oversized bodies before application parsing."""

    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, status_code: int, detail: str
    ) -> None:
        await JSONResponse({"detail": detail}, status_code=status_code)(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        encodings = [
            value.strip().lower() for key, value in headers if key.lower() == b"content-encoding"
        ]
        if any(value not in {b"", b"identity"} for value in encodings):
            await self._reject(
                scope, receive, send, 415, "Compressed request bodies are not accepted"
            )
            return

        lengths = [value.strip() for key, value in headers if key.lower() == b"content-length"]
        if len(lengths) > 1 or (lengths and b"," in lengths[0]):
            await self._reject(scope, receive, send, 400, "Invalid Content-Length")
            return
        if lengths:
            try:
                declared = int(lengths[0].decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await self._reject(scope, receive, send, 400, "Invalid Content-Length")
                return
            if declared < 0:
                await self._reject(scope, receive, send, 400, "Invalid Content-Length")
                return
            if declared > self.max_bytes:
                await self._reject(scope, receive, send, 413, "Request body too large")
                return

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await self._reject(scope, receive, send, 413, "Request body too large")
                return
            if not message.get("more_body", False):
                break

        buffered_body = bytes(body)
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": buffered_body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


def configured_rate_limit() -> int:
    try:
        limit = int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "120"))
    except ValueError as exc:
        raise ValueError("API rate limit must be an integer") from exc
    if not 1 <= limit <= 10_000:
        raise ValueError("API rate limit must be between 1 and 10,000")
    return limit


class RateLimitMiddleware:
    """Apply a bounded fixed-window quota without retaining bearer credentials."""

    def __init__(
        self,
        app: ASGIApp,
        requests_per_minute: int = 120,
        max_buckets: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_minute < 1 or max_buckets < 1:
            raise ValueError("Rate limit and bucket bound must be positive")
        self.app = app
        self.limit = requests_per_minute
        self.max_buckets = max_buckets
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._lock = Lock()

    @staticmethod
    def _key(scope: Scope) -> str:
        authorization = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"authorization"
            ),
            "",
        )
        parts = authorization.split(" ")
        if (
            len(parts) == 2
            and parts[0].casefold() == "bearer"
            and parts[1]
            and not any(character.isspace() for character in parts[1])
        ):
            fingerprint = hashlib.sha256(parts[1].encode()).hexdigest()
            return f"credential:{fingerprint}"
        client = scope.get("client")
        return f"client:{client[0] if client else 'unknown'}"

    def _consume(self, key: str) -> tuple[bool, int, int]:
        now = self.clock()
        with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= 60:
                start, count = now, 0
            if key not in self._buckets and len(self._buckets) >= self.max_buckets:
                self._buckets.popitem(last=False)
            allowed = count < self.limit
            if allowed:
                count += 1
            self._buckets[key] = (start, count)
            self._buckets.move_to_end(key)
            remaining = max(0, self.limit - count)
            retry_after = max(1, math.ceil(start + 60 - now))
            return allowed, remaining, retry_after

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/v1" or path.startswith("/v1/")):
            await self.app(scope, receive, send)
            return

        allowed, remaining, retry_after = self._consume(self._key(scope))
        rate_headers = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(remaining),
            "RateLimit-Reset": str(retry_after),
        }
        if not allowed:
            rate_headers["Retry-After"] = str(retry_after)
            await JSONResponse(
                {"detail": "Rate limit exceeded"}, status_code=429, headers=rate_headers
            )(scope, receive, send)
            return

        async def rate_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in rate_headers.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, rate_send)
