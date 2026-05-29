import time
import unittest

from runtime.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


class CircuitBreakerTests(unittest.TestCase):
    def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_seconds=60)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_opens_after_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_seconds=60)
        cb.record_failure("e1")
        cb.record_failure("e2")
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_before_call_passes_when_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_seconds=60)
        cb.before_call()  # must not raise

    def test_before_call_raises_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_seconds=60)
        cb.record_failure("e1")
        with self.assertRaises(CircuitOpenError):
            cb.before_call()

    def test_half_open_after_recovery(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.05)
        cb.record_failure("e1")
        time.sleep(0.1)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_success_closes_from_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.05)
        cb.record_failure("e1")
        time.sleep(0.1)
        cb.before_call()  # allowed in HALF_OPEN
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_seconds=60)
        cb.record_failure("e1")
        cb.record_failure("e2")
        cb.record_success()
        self.assertEqual(cb._failure_count, 0)

    def test_failure_in_half_open_reopens(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.05)
        cb.record_failure("e1")
        time.sleep(0.1)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        cb.record_failure("probe failed")
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_status_dict(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_seconds=30)
        d = cb.status_dict()
        self.assertEqual(d["state"], "closed")
        self.assertEqual(d["recovery_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
