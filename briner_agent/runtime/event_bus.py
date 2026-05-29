"""
briner_agent/runtime/event_bus.py

Minimal synchronous pub/sub event bus. No external dependencies.
All handlers are called synchronously in the publisher's thread.
Subscribers that raise are logged but do not propagate.

File state machine:
  DETECTED → QUEUED → PROCESSING → MOVED
                                 → IGNORED
                                 → ERROR
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


class FileState(str, Enum):
    DETECTED    = "detected"
    QUEUED      = "queued"
    PROCESSING  = "processing"
    CLASSIFIED  = "classified"
    MOVED       = "moved"
    IGNORED     = "ignored"
    ERROR       = "error"


@dataclass(frozen=True)
class FileEvent:
    state: FileState
    filepath: str
    filename: str
    category: str | None = None
    reason: str | None = None
    decision_source: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def short_label(self) -> str:
        """Single-line summary for tray display."""
        name = self.filename[:35] + "…" if len(self.filename) > 35 else self.filename
        if self.state in (FileState.MOVED, FileState.CLASSIFIED) and self.category:
            return f"{name} → {self.category.split('/')[-1]}"
        return f"{name} [{self.state.value}]"


Handler = Callable[[FileEvent], None]


class EventBus:
    """
    Singleton pub/sub bus.

    Usage:
        bus = EventBus()
        bus.subscribe(handler_fn)
        bus.publish(FileEvent(state=FileState.DETECTED, filepath=..., filename=...))
    """

    _instance: "EventBus | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._handlers: list[Handler] = []
                inst._lock = threading.Lock()
                cls._instance = inst
        return cls._instance

    def subscribe(self, handler: Handler):
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def unsubscribe(self, handler: Handler):
        with self._lock:
            self._handlers = [h for h in self._handlers if h != handler]

    def publish(self, event: FileEvent):
        with self._lock:
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("EventBus handler error for %s", event.filename)


# Module-level singleton convenience
bus = EventBus()
