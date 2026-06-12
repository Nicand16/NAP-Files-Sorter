"""
Smoke test de la ventana de NAPMonitor: construye la UI completa, inyecta
filas de ejemplo y verifica el filtrado en vivo. Se salta automaticamente
en entornos sin display (CI headless).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_root():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        return root
    except Exception:
        return None


class MonitorUITests(unittest.TestCase):
    def setUp(self):
        self.root = _make_root()
        if self.root is None:
            self.skipTest("Sin display grafico disponible")
        try:
            import monitor
        except Exception as exc:  # pystray/PIL pueden faltar en CI headless
            self.root.destroy()
            self.skipTest(f"Dependencias de UI no disponibles: {exc}")
        self.monitor = monitor
        self.app = monitor.NAPMonitorApp(self.root)

    def tearDown(self):
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass

    def _sample_rows(self):
        return [
            {"hora": "10:00:00", "archivo": "tarea_calculo.pdf", "categoria": "Universidad y Estudio/Actividades y Tareas",
             "fuente": "rule", "accion": "move", "razon": "keyword tarea"},
            {"hora": "10:00:05", "archivo": "foto_001.jpg", "categoria": "Multimedia/Imagenes y Capturas",
             "fuente": "cache", "accion": "move", "razon": "cache"},
            {"hora": "10:00:09", "archivo": "roto.bin", "categoria": "error",
             "fuente": "system", "accion": "error", "razon": "PermissionError"},
        ]

    def test_renders_rows_in_tree(self):
        self.app._all_rows = self._sample_rows()
        self.app._render_rows()

        self.assertEqual(len(self.app._tree.get_children()), 3)

    def test_filter_narrows_rows(self):
        self.app._all_rows = self._sample_rows()
        self.app._filter_var.set("calculo")  # dispara _render_rows via trace

        items = self.app._tree.get_children()
        self.assertEqual(len(items), 1)
        values = self.app._tree.item(items[0])["values"]
        self.assertIn("tarea_calculo.pdf", values)

    def test_error_rows_get_error_tag(self):
        self.app._all_rows = self._sample_rows()
        self.app._filter_var.set("")
        items = self.app._tree.get_children()
        error_item = items[-1]

        self.assertIn("error_row", self.app._tree.item(error_item)["tags"])

    def test_save_api_key_rejects_short_values(self):
        # No debe escribir nada ni lanzar dialogos bloqueantes para keys obvias invalidas
        import tkinter.messagebox as mb
        original = mb.showerror
        calls = []
        mb.showerror = lambda *a, **k: calls.append(a)
        try:
            saved = self.app._save_api_key("GROQ_API_KEY", "abc", "gsk_", "Groq")
        finally:
            mb.showerror = original

        self.assertFalse(saved)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
