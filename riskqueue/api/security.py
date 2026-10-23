"""Dependency-free HTTP security controls for the operator API."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_INLINE_STYLE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)


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
                headers["Content-Security-Policy"] = (
                    self.operator_policy
                    if scope.get("path") == "/operator"
                    else "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                )
            await send(message)

        await self.app(scope, receive, secure_send)
