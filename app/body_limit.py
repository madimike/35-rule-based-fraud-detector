
"""Limit the size of incoming HTTP request bodies."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Reject HTTP requests whose body exceeds the configured limit."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = 65_536,
    ) -> None:
        if max_body_bytes < 0:
            raise ValueError("max_body_bytes must not be negative")

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = None

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    await self._reject(send)
                    return

                if content_length < 0:
                    await self._reject(send)
                    return
                break

        if (
            content_length is not None
            and content_length > self.max_body_bytes
        ):
            await self._reject(send)
            return

        body = bytearray()

        while True:
            message = await receive()

            if message["type"] == "http.disconnect":
                return

            if message["type"] != "http.request":
                continue

            chunk = message.get("body", b"")

            if len(body) + len(chunk) > self.max_body_bytes:
                await self._reject(send)
                return

            body.extend(chunk)

            if not message.get("more_body", False):
                break

        body_bytes = bytes(body)
        body_sent = False

        async def replay_body() -> Message:
            nonlocal body_sent

            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": body_bytes,
                    "more_body": False,
                }

            return await receive()

        await self.app(scope, replay_body, send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(
            {
                "detail": (
                    "Request body too large. Maximum size is "
                    f"{self.max_body_bytes} bytes."
                )
            }
        ).encode("utf-8")

        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
