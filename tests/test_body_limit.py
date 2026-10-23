
import asyncio

from starlette.types import Message, Scope

from app.body_limit import RequestBodyLimitMiddleware


def run_middleware_with_chunks(chunks: list[bytes]):
    received_by_app = False
    sent_messages: list[Message] = []
    chunk_index = 0

    async def app(scope, receive, send):
        nonlocal received_by_app
        received_by_app = True

        while True:
            message = await receive()
            if not message.get("more_body", False):
                break

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"ok",
            }
        )

    async def receive():
        nonlocal chunk_index

        if chunk_index < len(chunks):
            chunk = chunks[chunk_index]
            chunk_index += 1
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": chunk_index < len(chunks),
            }

        return {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }

    async def send(message):
        sent_messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/transactions",
        "raw_path": b"/transactions",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    }

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=65_536)
    asyncio.run(middleware(scope, receive, send))

    return received_by_app, sent_messages


def test_oversized_chunked_body_is_rejected_before_app_runs():
    received_by_app, messages = run_middleware_with_chunks(
        [b"x" * 40_000, b"y" * 30_000]
    )

    assert received_by_app is False
    assert messages[0]["status"] == 413

    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert b"Request body too large" in response_body


def test_chunked_body_at_limit_reaches_app():
    received_by_app, messages = run_middleware_with_chunks(
        [b"x" * 32_768, b"y" * 32_768]
    )

    assert received_by_app is True
    assert messages[0]["status"] == 200
