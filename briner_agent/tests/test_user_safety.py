"""
Tests de las protecciones contra errores de usuario (v1.2.0):

- Carpetas peligrosas rechazadas como workspace (raiz de disco, sistema, perfil).
- Archivos muy recientes (descargas en curso) no se registran todavia.
- Candado de instancia unica para NAPBackground.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.settings_manager import dangerous_workspace_reason, validate_watch_directory
from modules.periodic_scanner import scan_directory_once
from runtime.single_instance import SingleInstanceLock


class FakeDb:
    def __init__(self):
        self.registered = []

    def register_file(self, *info, **kwargs):
        self.registered.append(info)
        return True


class DangerousWorkspaceTests(unittest.TestCase):
    def test_rejects_drive_root(self):
        anchor = Path.cwd().anchor  # "C:\\" en Windows, "/" en POSIX
        self.assertIsNotNone(dangerous_workspace_reason(anchor))
        with self.assertRaises(ValueError):
            validate_watch_directory(anchor)

    def test_rejects_user_home_root(self):
        self.assertIsNotNone(dangerous_workspace_reason(Path.home()))

    def test_rejects_system_directories(self):
        for env_name in ("SystemRoot", "ProgramFiles", "APPDATA", "LOCALAPPDATA"):
            value = os.environ.get(env_name)
            if not value:
                continue
            with self.subTest(env=env_name):
                self.assertIsNotNone(dangerous_workspace_reason(value))

    def test_rejects_parent_of_system_directory(self):
        appdata = os.environ.get("APPDATA")
        if not appdata:
            self.skipTest("APPDATA no definido en este entorno")
        parent = Path(appdata).parent  # ej. C:\Users\X\AppData
        self.assertIsNotNone(dangerous_workspace_reason(parent))

    def test_accepts_regular_subfolder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(dangerous_workspace_reason(temp_dir))
            self.assertEqual(validate_watch_directory(temp_dir), str(Path(temp_dir).resolve()))


class MinFileAgeTests(unittest.TestCase):
    def test_skips_files_modified_recently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "descargando.pdf").write_text("partial", encoding="utf-8")
            db = FakeDb()

            count = scan_directory_once(root, db, {"monitoring": {"min_file_age_seconds": 60}})

            self.assertEqual(count, 0)
            self.assertEqual(db.registered, [])

    def test_registers_files_older_than_min_age(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "estable.pdf"
            path.write_text("done", encoding="utf-8")
            old = time.time() - 120
            os.utime(path, (old, old))
            db = FakeDb()

            count = scan_directory_once(root, db, {"monitoring": {"min_file_age_seconds": 60}})

            self.assertEqual(count, 1)
            self.assertEqual(db.registered[0][0], "estable.pdf")

    def test_ignores_browser_partial_download_patterns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("a.crdownload", "b.part", "c.partial", "d.download", "e.opdownload"):
                (root / name).write_text("x", encoding="utf-8")
            db = FakeDb()

            count = scan_directory_once(root, db, {"monitoring": {"min_file_age_seconds": 0}})

            self.assertEqual(count, 0)


class SingleInstanceLockTests(unittest.TestCase):
    def test_second_acquire_fails_while_locked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".nap.lock"
            first = SingleInstanceLock(lock_path)
            second = SingleInstanceLock(lock_path)

            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
            finally:
                first.release()

    def test_release_allows_reacquire(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".nap.lock"
            first = SingleInstanceLock(lock_path)
            second = SingleInstanceLock(lock_path)

            self.assertTrue(first.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_acquire_is_idempotent_for_same_holder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".nap.lock"
            lock = SingleInstanceLock(lock_path)

            self.assertTrue(lock.acquire())
            self.assertTrue(lock.acquire())
            lock.release()


if __name__ == "__main__":
    unittest.main()
