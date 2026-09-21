"""ASGI upload limits enforced before multipart form parsing."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True)
class UploadPolicy:
    """Limits for the complete multipart request body."""

    max_body_bytes: int
    max_concurrent: int
    timeout_seconds: float


class _UploadBodyTooLarge(Exception):
    pass


class UploadRequestGuard:
    """Reject oversized, excessive, or stalled uploads before route parsing."""

    def __init__(
        self,
        app: ASGIApp,
        policy_provider: Callable[[], UploadPolicy],
    ) -> None:
        self.app = app
        self.policy_provider = policy_provider
        self._active_uploads = 0
        self._state_lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_upload(scope):
            await self.app(scope, receive, send)
            return

        policy = self.policy_provider()
        declared_length = self._content_length(scope)
        if declared_length is None:
            pass
        elif declared_length < 0:
            await self._respond(scope, receive, send, 400, "Content-Length 无效")
            return
        elif declared_length > policy.max_body_bytes:
            await self._respond(scope, receive, send, 413, "上传请求超过大小限制")
            return

        if not self._reserve(policy.max_concurrent):
            await self._respond(
                scope,
                receive,
                send,
                429,
                "并发上传过多，请稍后重试",
                headers={"Retry-After": "1"},
            )
            return

        received = 0
        response_started = False

        async def guarded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > policy.max_body_bytes:
                    raise _UploadBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await asyncio.wait_for(
                self.app(scope, guarded_receive, tracked_send),
                timeout=policy.timeout_seconds,
            )
        except _UploadBodyTooLarge:
            if not response_started:
                await self._respond(
                    scope, receive, send, 413, "上传请求超过大小限制"
                )
        except asyncio.TimeoutError:
            if not response_started:
                await self._respond(scope, receive, send, 408, "上传请求超时")
        finally:
            self._release()

    @staticmethod
    def _is_upload(scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/files/upload"
        )

    @staticmethod
    def _content_length(scope: Scope):
        for name, raw_value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                return -1
        return None

    def _reserve(self, maximum: int) -> bool:
        with self._state_lock:
            if self._active_uploads >= max(1, maximum):
                return False
            self._active_uploads += 1
            return True

    def _release(self) -> None:
        with self._state_lock:
            self._active_uploads -= 1

    @staticmethod
    async def _respond(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
        *,
        headers=None,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers=headers,
        )
        await response(scope, receive, send)
