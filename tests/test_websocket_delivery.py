"""WebSocket 消息分发回归测试。"""

import asyncio
import queue
import unittest
from collections import defaultdict
from contextlib import suppress

from web.ws.handlers import _broadcast_dispatcher, _message_targets


class WebSocketDeliveryTests(unittest.TestCase):
    def test_job_message_is_fanned_out_only_to_matching_connections(self):
        first = object()
        second = object()
        other = object()
        connections = defaultdict(set, {
            "job-1": {first, second},
            "job-2": {other},
        })

        self.assertCountEqual(
            _message_targets(connections, "job-1"),
            [first, second],
        )

    def test_wildcard_log_is_fanned_out_to_all_connections(self):
        first = object()
        second = object()
        connections = defaultdict(set, {
            "job-1": {first},
            "job-2": {second},
        })

        self.assertCountEqual(
            _message_targets(connections, "*"),
            [first, second],
        )


class WebSocketDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_queue_message_reaches_every_matching_connection(self):
        class FakeWebSocket:
            def __init__(self):
                self.messages = []

            async def send(self, payload):
                self.messages.append(payload)

        first = FakeWebSocket()
        second = FakeWebSocket()
        messages = queue.Queue()
        connections = defaultdict(set, {"job-1": {first, second}})
        dispatcher = asyncio.create_task(
            _broadcast_dispatcher(messages, connections, "status")
        )
        messages.put({"type": "state_update", "job_id": "job-1", "data": {}})

        for _ in range(100):
            if first.messages and second.messages:
                break
            await asyncio.sleep(0.001)

        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher

        self.assertEqual(len(first.messages), 1)
        self.assertEqual(len(second.messages), 1)


if __name__ == "__main__":
    unittest.main()
