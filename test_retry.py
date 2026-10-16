import unittest

from nexora.core.retry import backoff_delay, retry_async


class Retry(unittest.IsolatedAsyncioTestCase):
    def test_backoff_bounds(self):
        self.assertEqual(backoff_delay(1, 2, 300, rng=lambda: 1.0), 2)
        self.assertEqual(backoff_delay(4, 2, 300, rng=lambda: 1.0), 16)
        self.assertEqual(backoff_delay(20, 2, 300, rng=lambda: 1.0), 300)

    async def test_retries_then_succeeds(self):
        calls, sleeps = [], []

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError
            return "ok"

        async def fake_sleep(s):
            sleeps.append(s)

        self.assertEqual(await retry_async(flaky, sleep=fake_sleep), "ok")
        self.assertEqual((len(calls), len(sleeps)), (3, 2))

    async def test_gives_up_after_max_attempts(self):
        async def bad():
            raise ConnectionError

        async def nosleep(s):
            pass

        with self.assertRaises(ConnectionError):
            await retry_async(bad, max_attempts=3, sleep=nosleep)
