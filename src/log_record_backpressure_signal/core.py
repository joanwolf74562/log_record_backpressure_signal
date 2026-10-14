"""Core implementation of the backpressure signal.

This module monitors the depth of a logging handler's internal queue and
emits a single structured warning event when that depth crosses a threshold.

Design decisions
----------------
* We treat the handler's ``queue`` attribute as the source of truth for
  depth.  In the standard library only ``QueueHandler`` and its subclasses
  carry one, so we raise ``TypeError`` early for anything else rather than
  silently swallowing the problem.
* A callback is used instead of calling ``logging.warning`` directly so the
  library stays free of side-effects on the global logging state.  The caller
  decides whether the event becomes a log record, a metric, or a no-op.
* The monitor holds no background thread.  ``check()`` is synchronous and
  deterministic, which makes the behaviour trivial to test.
"""

from __future__ import annotations

import logging
import logging.handlers
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class BackpressureSignal:
    """Structured payload emitted when the queue depth crosses ``threshold``.

    The fields are deliberately primitive (``str`` / ``int``) so the signal can
    be serialised to JSON or fed into a metrics pipeline without bespoke
    adapters.
    """

    handler_name: str
    queue_depth: int
    threshold: int

    def to_dict(self) -> dict[str, Any]:
        """Return a plain ``dict`` view of the signal.

        Kept separate from the dataclass itself so callers who want a mapping
        get one without mutating or copying the frozen instance.
        """
        return {
            "handler_name": self.handler_name,
            "queue_depth": self.queue_depth,
            "threshold": self.threshold,
        }


class BackpressureMonitor:
    """Watch a handler's queue depth and fire a callback once per overrun.

    The monitor is edge-triggered: it fires exactly once when depth rises from
    ``<= threshold`` to ``> threshold``, and will not fire again until depth
    has returned to ``<= threshold`` at least once.  This prevents a flooded
    handler from generating a warning on every single record while still
    re-arming the signal once the queue has drained.

    Parameters
    ----------
    handler:
        A :class:`logging.Handler` whose ``queue`` attribute is a
        :class:`queue.Queue`.  ``QueueHandler`` and subclasses qualify.
    threshold:
        Depth that must be strictly exceeded to trigger.  Must be ``>= 0``.
        A value of ``0`` triggers on every non-empty check.
    callback:
        Called with a :class:`BackpressureSignal` when the threshold is
        crossed upward.  Exceptions raised by the callback are propagated
        unchanged — see the README for the rationale.
    """

    def __init__(
        self,
        handler: logging.Handler,
        threshold: int,
        callback: Callable[[BackpressureSignal], None],
    ) -> None:
        if not isinstance(handler, logging.Handler):
            raise TypeError("handler must be a logging.Handler instance")
        if not isinstance(threshold, int) or isinstance(threshold, bool):
            raise TypeError("threshold must be an int")
        if threshold < 0:
            raise ValueError("threshold must be >= 0")
        if not callable(callback):
            raise TypeError("callback must be callable")

        # We require a real ``QueueHandler`` (or subclass) because the entire
        # premise of the library is observing a queue.  Duck-typing would let
        # unrelated handlers through and produce confusing AttributeError
        # traces deep inside ``check``.
        if not isinstance(handler, logging.handlers.QueueHandler):
            raise TypeError("handler must be a QueueHandler instance")

        self._handler = handler
        self._threshold = threshold
        self._callback = callback
        self._armed = True

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def armed(self) -> bool:
        """Whether the monitor will fire on the next upward crossing."""
        return self._armed

    def check(self) -> Optional[BackpressureSignal]:
        """Inspect the queue depth and fire the callback if warranted.

        Returns the :class:`BackpressureSignal` if one was emitted, otherwise
        ``None``.  Safe to call repeatedly from any thread that can safely
        touch ``handler.queue``.
        """
        # ``qsize`` is documented as unreliable on macOS but we treat it as
        # authoritative because the caller chose the platform and the
        # alternative (peeking the deque) is not portable across handlers.
        depth = self._handler.queue.qsize()

        if depth > self._threshold and self._armed:
            signal = BackpressureSignal(
                handler_name=self._handler.name,
                queue_depth=depth,
                threshold=self._threshold,
            )
            self._armed = False
            self._callback(signal)
            return signal

        if depth <= self._threshold and not self._armed:
            self._armed = True

        return None

    def reset(self) -> None:
        """Force the monitor back into the armed state without checking depth."""
        self._armed = True
