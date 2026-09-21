import asyncio
import unittest

from openhpc_webui.services.upload_guard import UploadPolicy, UploadRequestGuard


def upload_scope(headers=None):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/files/upload",
        "raw_path": b"/api/files/upload",
        "query_string": b"",
        "headers": headers or [],
        "client": ("192.0.2.1", 1234),
        "server": ("testserver", 80),
    }


async def invoke(app, scope, messages):
    sent = []
    pending = list(messages)

    async def receive():
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def consume_body(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


class UploadRequestGuardTests(unittest.TestCase):
    @staticmethod
    def guarded_app(app=consume_body, **changes):
        values = {
            "max_body_bytes": 8,
            "max_concurrent": 2,
            "timeout_seconds": 1,
        }
        values.update(changes)
        return UploadRequestGuard(app, policy_provider=lambda: UploadPolicy(**values))

    def test_declared_oversized_body_is_rejected_before_downstream(self):
        called = []

        async def downstream(scope, receive, send):
            called.append(True)

        app = self.guarded_app(downstream)
        sent = asyncio.run(
            invoke(
                app,
                upload_scope([(b"content-length", b"9")]),
                [{"type": "http.request", "body": b"", "more_body": False}],
            )
        )

        self.assertEqual(sent[0]["status"], 413)
        self.assertEqual(called, [])

    def test_streamed_body_is_stopped_when_actual_bytes_exceed_limit(self):
        app = self.guarded_app()
        sent = asyncio.run(
            invoke(
                app,
                upload_scope(),
                [
                    {"type": "http.request", "body": b"12345", "more_body": True},
                    {"type": "http.request", "body": b"6789", "more_body": False},
                ],
            )
        )

        self.assertEqual(sent[0]["status"], 413)

    def test_body_at_limit_reaches_downstream(self):
        app = self.guarded_app()
        sent = asyncio.run(
            invoke(
                app,
                upload_scope([(b"content-length", b"8")]),
                [{"type": "http.request", "body": b"12345678", "more_body": False}],
            )
        )

        self.assertEqual(sent[0]["status"], 204)

    def test_non_upload_requests_are_not_limited(self):
        app = self.guarded_app()
        scope = upload_scope([(b"content-length", b"100")])
        scope["path"] = "/api/files/content"
        sent = asyncio.run(
            invoke(
                app,
                scope,
                [{"type": "http.request", "body": b"x" * 100, "more_body": False}],
            )
        )

        self.assertEqual(sent[0]["status"], 204)

    def test_slow_upload_is_cancelled(self):
        async def slow_downstream(scope, receive, send):
            await asyncio.sleep(0.05)

        app = self.guarded_app(slow_downstream, timeout_seconds=0.01)
        sent = asyncio.run(
            invoke(
                app,
                upload_scope(),
                [{"type": "http.request", "body": b"", "more_body": False}],
            )
        )

        self.assertEqual(sent[0]["status"], 408)

    def test_excess_concurrent_upload_is_rejected(self):
        async def scenario():
            entered = asyncio.Event()
            release = asyncio.Event()

            async def blocked_downstream(scope, receive, send):
                entered.set()
                await release.wait()
                await send(
                    {"type": "http.response.start", "status": 204, "headers": []}
                )
                await send({"type": "http.response.body", "body": b""})

            app = self.guarded_app(
                blocked_downstream,
                max_concurrent=1,
            )
            message = {"type": "http.request", "body": b"", "more_body": False}
            first = asyncio.create_task(invoke(app, upload_scope(), [message]))
            await entered.wait()
            second = await invoke(app, upload_scope(), [message])
            release.set()
            await first
            return second

        sent = asyncio.run(scenario())

        self.assertEqual(sent[0]["status"], 429)
        self.assertIn((b"retry-after", b"1"), sent[0]["headers"])


if __name__ == "__main__":
    unittest.main()
