"""
Tests del manejo de recuperacion de circuit breakers en NAPOrchestrator.

Cubre la regresion donde un error de limite diario subia recovery_seconds a
3600 y nunca volvia al valor base, dejando el breaker lento para siempre.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_orchestrator import NAPOrchestrator
from db.database_manager import DatabaseManager
from runtime.circuit_breaker import CircuitState

DAILY_LIMIT_MSG = "429 Rate limit reached: requests per day exceeded"
MINUTE_LIMIT_MSG = "429 too many requests: rate limit per minute"


def _make_orchestrator(temp_dir: str) -> NAPOrchestrator:
    db = DatabaseManager(str(Path(temp_dir) / "nap.db"))
    config = {
        "processing": {
            "groq_circuit_recovery_seconds": 65,
            "gemini_circuit_recovery_seconds": 65,
            "groq_daily_circuit_recovery_seconds": 3600,
            "gemini_daily_circuit_recovery_seconds": 3600,
        }
    }
    return NAPOrchestrator(config=config, db_manager=db, workspace_dir=temp_dir)


class CircuitRecoveryResetTests(unittest.TestCase):
    def test_daily_limit_extends_recovery_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            orch = _make_orchestrator(temp_dir)

            for _ in range(3):
                orch._record_api_failure(DAILY_LIMIT_MSG, provider="groq")

            self.assertEqual(orch._groq_circuit.state, CircuitState.OPEN)
            self.assertEqual(orch._groq_circuit.recovery_seconds, 3600)

    def test_success_restores_base_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            orch = _make_orchestrator(temp_dir)

            for _ in range(3):
                orch._record_api_failure(DAILY_LIMIT_MSG, provider="groq")
            orch._record_api_success("groq")

            self.assertEqual(orch._groq_circuit.state, CircuitState.CLOSED)
            self.assertEqual(orch._groq_circuit.recovery_seconds, 65)

    def test_minute_limit_after_daily_uses_base_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            orch = _make_orchestrator(temp_dir)

            orch._record_api_failure(DAILY_LIMIT_MSG, provider="groq")
            self.assertEqual(orch._groq_circuit.recovery_seconds, 3600)

            orch._record_api_failure(MINUTE_LIMIT_MSG, provider="groq")
            self.assertEqual(orch._groq_circuit.recovery_seconds, 65)

    def test_gemini_circuit_is_independent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            orch = _make_orchestrator(temp_dir)

            for _ in range(3):
                orch._record_api_failure(DAILY_LIMIT_MSG, provider="gemini")

            self.assertEqual(orch._gemini_circuit.state, CircuitState.OPEN)
            self.assertEqual(orch._gemini_circuit.recovery_seconds, 3600)
            self.assertEqual(orch._groq_circuit.state, CircuitState.CLOSED)
            self.assertEqual(orch._groq_circuit.recovery_seconds, 65)


if __name__ == "__main__":
    unittest.main()
