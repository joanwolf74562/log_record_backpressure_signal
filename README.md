# log_record_backpressure_signal

Monitors a `logging.handlers.QueueHandler`'s queue depth and calls a callback exactly once when the depth rises above a configurable threshold.

```python
import logging, logging.handlers, queue
from log_record_backpressure_signal import BackpressureMonitor, BackpressureSignal

handler = logging.handlers.QueueHandler(queue.Queue())

def on_backpressure(sig: BackpressureSignal) -> None:
    print(f"{sig.handler_name} depth {sig.queue_depth} > {sig.threshold}")

monitor = BackpressureMonitor(handler, threshold=100, callback=on_backpressure)

# call monitor.check() from whatever loop feeds or drains the handler
monitor.check()
```

## Why

`QueueHandler` is the usual way to hand log records off to a worker thread, but nothing in the standard library tells you when the queue is growing faster than the worker can drain it. A quietly saturated queue turns into dropped records (once `maxsize` is hit) or unbounded memory growth (when it is not). This library gives you a single, cheap signal — "the queue is now deeper than you said you could tolerate" — that you can wire into metrics, an alert, or a circuit breaker.

The trade-off: the monitor is poll-based, not event-based. It only knows what `handler.queue.qsize()` reports at the moment you call `check()`. On platforms where `qsize()` is approximate (macOS, some BSDs) the signal is correspondingly approximate. We chose to trust `qsize()` rather than wrap the queue's `put`/`get`, because wrapping those would change the handler's threading contract and defeat the point of using a stdlib handler in the first place.

## Behaviour worth knowing

The signal is **edge-triggered**. After firing once it will not fire again until the queue has drained back to `<= threshold` at least once (or you call `reset()`). This prevents a flooded handler from emitting a warning on every record, but it also means you can miss repeated pressure if the queue hovers above the threshold without ever draining. If you need level-triggered semantics, call `reset()` after every `check()`.

The callback is invoked synchronously inside `check()`, and exceptions it raises propagate out of `check()` unchanged. The monitor is left disarmed in that case — it does not pretend the signal was delivered. Call `reset()` once you have handled the failure.

Only `logging.handlers.QueueHandler` (or a subclass) is accepted. A plain `StreamHandler` has no queue to observe, so passing one raises `TypeError` immediately rather than failing later with an attribute error.
