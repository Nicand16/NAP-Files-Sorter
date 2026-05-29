import unittest

from runtime.event_bus import EventBus, FileEvent, FileState


class EventBusTests(unittest.TestCase):
    def setUp(self):
        EventBus._instance = None

    def test_subscribe_and_receive(self):
        bus = EventBus()
        received = []
        bus.subscribe(received.append)
        event = FileEvent(state=FileState.DETECTED, filepath="/x/a.pdf", filename="a.pdf")
        bus.publish(event)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].state, FileState.DETECTED)

    def test_handler_exception_does_not_propagate(self):
        bus = EventBus()
        def bad_handler(e): raise RuntimeError("boom")
        bus.subscribe(bad_handler)
        event = FileEvent(state=FileState.MOVED, filepath="/x/a.pdf", filename="a.pdf")
        bus.publish(event)  # must not raise

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe(received.append)
        bus.unsubscribe(received.append)
        bus.publish(FileEvent(state=FileState.QUEUED, filepath="/x/b.txt", filename="b.txt"))
        self.assertEqual(len(received), 0)

    def test_short_label_with_category(self):
        ev = FileEvent(
            state=FileState.MOVED,
            filepath="/x/a.pdf",
            filename="a.pdf",
            category="Universidad y Estudio/Actividades y Tareas",
        )
        self.assertIn("Actividades y Tareas", ev.short_label())

    def test_short_label_no_category(self):
        ev = FileEvent(state=FileState.ERROR, filepath="/x/a.pdf", filename="a.pdf")
        self.assertIn("[error]", ev.short_label())

    def test_multiple_subscribers(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe(a.append)
        bus.subscribe(b.append)
        bus.publish(FileEvent(state=FileState.PROCESSING, filepath="/x/c.txt", filename="c.txt"))
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)


if __name__ == "__main__":
    unittest.main()
