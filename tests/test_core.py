"""Tests for log_record_backpressure_signal.core.

All tests are deterministic: no sleeping, no wall-clock comparisons, no
float equality.  Where timing would normally matter we instead drive the
monitor explicitly via ``check()``.
"""

import logging
import logging.handlers
import queue
import unittest

from log_record_backpressure_signal import BackpressureMonitor, BackpressureSignal


def _make_handler(name: str = "test-handler") -> logging.handlers.QueueHandler:
    """Build a QueueHandler whose queue we can fill for testing."""
    handler = logging.handlers.QueueHandler(queue.Queue())
    handler.name = name
    return handler


class BackpressureSignalTests(unittest.TestCase):
    def test_to_dict_round_trip(self) -> None:
        sig = BackpressureSignal(
            handler_name="h", queue_depth=5, threshold=3
        )
        self.assertEqual(
            sig.to_dict(),
            {"handler_name": "h", "queue_depth": 5, "threshold": 3},
        )

    def test_is_frozen(self) -> None:
        sig = BackpressureSignal(handler_name="h", queue_depth=1, threshold=0)
        with self.assertRaises(Exception):
            sig.queue_depth = 2  # type: ignore[misc]


class BackpressureMonitorConstructionTests(unittest.TestCase):
    def test_rejects_non_handler(self) -> None:
        with self.assertRaises(TypeError):
            BackpressureMonitor("not-a-handler", 1, lambda s: None)  # type: ignore[arg-type]

    def test_rejects_non_queue_handler(self) -> None:
        with self.assertRaises(TypeError):
            BackpressureMonitor(logging.StreamHandler(), 1, lambda s: None)

    def test_rejects_negative_threshold(self) -> None:
        with self.assertRaises(ValueError):
            BackpressureMonitor(_make_handler(), -1, lambda s: None)

    def test_rejects_bool_threshold(self) -> None:
        # ``bool`` is a subclass of ``int``; we explicitly forbid it so a
        # stray ``True`` does not silently become threshold 1.
        with self.assertRaises(TypeError):
            BackpressureMonitor(_make_handler(), True, lambda s: None)  # type: ignore[arg-type]

    def test_rejects_non_callable(self) -> None:
        with self.assertRaises(TypeError):
            BackpressureMonitor(_make_handler(), 1, "nope")  # type: ignore[arg-type]

    def test_threshold_zero_is_allowed(self) -> None:
        BackpressureMonitor(_make_handler(), 0, lambda s: None)


class BackpressureMonitorBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = _make_handler("unit-handler")
        self.fired: list[BackpressureSignal] = []
        self.monitor = BackpressureMonitor(
            self.handler, threshold=3, callback=self.fired.append
        )

    def _enqueue(self, n: int) -> None:
        for _ in range(n):
            self.handler.queue.put(logging.LogRecord(
                name="x", level=logging.INFO, pathname="", lineno=0,
                msg="", args=None, exc_info=None,
            ))

    def test_no_signal_below_threshold(self) -> None:
        self._enqueue(3)
        self.assertIsNone(self.monitor.check())
        self.assertEqual(self.fired, [])

    def test_fires_when_crossing_threshold(self) -> None:
        self._enqueue(4)
        sig = self.monitor.check()
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.handler_name, "unit-handler")
        self.assertEqual(sig.queue_depth, 4)
        self.assertEqual(sig.threshold, 3)
        self.assertEqual(self.fired, [sig])

    def test_edge_triggered_no_repeat_while_above(self) -> None:
        self._enqueue(4)
        self.monitor.check()
        self._enqueue(2)  # now 6, still above 3
        sig = self.monitor.check()
        self.assertIsNone(sig)
        self.assertEqual(len(self.fired), 1)

    def test_rearms_after_draining_below_threshold(self) -> None:
        self._enqueue(4)
        self.monitor.check()
        # Drain completely.
        while not self.handler.queue.empty():
            self.handler.queue.get_nowait()
        # Re-arm should happen silently.
        self.assertIsNone(self.monitor.check())
        # Second crossing fires again.
        self._enqueue(4)
        sig = self.monitor.check()
        self.assertIsNotNone(sig)
        self.assertEqual(len(self.fired), 2)

    def test_reset_forces_rearm(self) -> None:
        self._enqueue(4)
        self.monitor.check()
        self.monitor.reset()
        # Still above threshold, so next check should fire again.
        sig = self.monitor.check()
        self.assertIsNotNone(sig)
        self.assertEqual(len(self.fired), 2)

    def test_callback_exception_propagates(self) -> None:
        boom_calls: list[BackpressureSignal] = []

        def boom(sig: BackpressureSignal) -> None:
            boom_calls.append(sig)
            raise RuntimeError("boom")

        monitor = BackpressureMonitor(self.handler, threshold=1, callback=boom)
        self._enqueue(2)
        with self.assertRaises(RuntimeError):
            monitor.check()
        self.assertEqual(len(boom_calls), 1)
        # Monitor stays disarmed because the callback failed before we could
        # consider the signal delivered.
        self.assertFalse(monitor.armed)

    def test_armed_property_reflects_state(self) -> None:
        self.assertTrue(self.monitor.armed)
        self._enqueue(4)
        self.monitor.check()
        self.assertFalse(self.monitor.armed)
        self.monitor.reset()
        self.assertTrue(self.monitor.armed)

    def test_threshold_property(self) -> None:
        self.assertEqual(self.monitor.threshold, 3)

    def test_zero_threshold_fires_on_first_record(self) -> None:
        fired: list[BackpressureSignal] = []
        monitor = BackpressureMonitor(
            self.handler, threshold=0, callback=fired.append
        )
        self._enqueue(1)
        sig = monitor.check()
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.queue_depth, 1)
        self.assertEqual(sig.threshold, 0)


if __name__ == "__main__":
    unittest.main()
