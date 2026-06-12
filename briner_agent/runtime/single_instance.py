"""
Candado de instancia unica basado en bloqueo de archivo a nivel de SO.

Evita que dos procesos NAPBackground organicen la misma carpeta a la vez
(doble procesamiento, movimientos duplicados y condiciones de carrera en la DB).
El bloqueo lo libera el sistema operativo automaticamente si el proceso muere,
por lo que no quedan candados huerfanos tras un crash.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class SingleInstanceLock:
    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        self._handle = None

    def acquire(self) -> bool:
        """Intenta tomar el candado. False si otra instancia ya lo tiene."""
        if self._handle is not None:
            return True
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.lock_path, "a+")
        except OSError as exc:
            # Si no se puede ni abrir el archivo de lock, no bloqueamos el arranque.
            logger.warning("No se pudo abrir el archivo de lock %s: %s", self.lock_path, exc)
            return True

        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError:
            pass  # el PID es solo informativo; el bloqueo ya esta tomado
        self._handle = handle
        return True

    def release(self):
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None
