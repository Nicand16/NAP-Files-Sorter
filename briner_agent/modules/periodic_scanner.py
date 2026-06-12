import fnmatch
import logging
import time
from pathlib import Path

from runtime.event_bus import FileEvent, FileState, bus

logger = logging.getLogger(__name__)

DEFAULT_IGNORED_FILENAMES = {".keep", "desktop.ini"}
DEFAULT_IGNORED_PATTERNS = ["~$*", "*.tmp", "*.temp", "*.crdownload", "*.part", "*.partial", "*.download", "*.opdownload"]
# Un archivo modificado hace menos de N segundos puede ser una descarga en curso
# que no usa extension temporal (ej. guardado directo). Se registra al proximo ciclo.
DEFAULT_MIN_FILE_AGE_SECONDS = 15


def _is_ignored(path: Path, ignored_filenames: set[str], ignored_patterns: list[str]) -> bool:
    name = path.name.casefold()
    if name in ignored_filenames:
        return True
    return any(fnmatch.fnmatch(name, pattern.casefold()) for pattern in ignored_patterns)


def _file_info(path: Path):
    stat = path.stat()
    return path.name, str(path.resolve()), path.suffix, stat.st_size, stat.st_mtime


def _category_roots(watch_path: Path, config: dict) -> set[Path]:
    roots = set()
    aliases = config.get("monitoring", {}).get("destination_aliases", {})
    for real_root in aliases.values():
        roots.add((watch_path / real_root).resolve())
    for rule in config.get("taxonomy", {}).get("categories", []):
        top = rule.get("category", "").split("/")[0]
        if top and top not in aliases:
            roots.add((watch_path / top).resolve())
    roots.add((watch_path / "Varios").resolve())
    roots.add((watch_path / "_NAP Quarantine").resolve())
    return roots


def _is_inside_any(path: Path, roots: set[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def scan_directory_once(watch_directory: str | Path, db_manager, config: dict | None = None) -> int:
    config = config or {}
    monitoring = config.get("monitoring", {})
    watch_path = Path(watch_directory).expanduser().resolve()
    ignored_filenames = {
        name.casefold()
        for name in monitoring.get("ignored_filenames", DEFAULT_IGNORED_FILENAMES)
    }
    ignored_patterns = monitoring.get("ignored_patterns", DEFAULT_IGNORED_PATTERNS)
    min_file_age = float(monitoring.get("min_file_age_seconds", DEFAULT_MIN_FILE_AGE_SECONDS))
    category_roots = _category_roots(watch_path, config) if monitoring.get("ignore_existing_categories", True) else set()

    if not watch_path.exists() or not watch_path.is_dir():
        logger.error("La carpeta de escaneo no existe o no es directorio: %s", watch_path)
        return 0

    detected = 0
    skipped_category = 0
    skipped_ignored = 0
    skipped_recent = 0
    now = time.time()
    files = watch_path.rglob("*") if monitoring.get("recursive", False) else watch_path.iterdir()
    for path in files:
        try:
            if not path.is_file():
                continue
            if _is_ignored(path, ignored_filenames, ignored_patterns):
                skipped_ignored += 1
                continue
            if _is_inside_any(path, category_roots):
                skipped_category += 1
                continue
            if min_file_age > 0 and (now - path.stat().st_mtime) < min_file_age:
                # Posible descarga/copia en curso: no registrar todavia.
                skipped_recent += 1
                continue
            if db_manager.register_file(*_file_info(path)):
                detected += 1
                bus.publish(FileEvent(
                    state=FileState.DETECTED,
                    filepath=str(path.resolve()),
                    filename=path.name,
                ))
        except OSError as exc:
            logger.warning("No se pudo registrar %s: %s", path, exc)

    # Scan directories at root level (always non-recursive: a folder is treated as a unit)
    detected_dirs = 0
    for path in watch_path.iterdir():
        try:
            if not path.is_dir():
                continue
            if _is_ignored(path, ignored_filenames, ignored_patterns):
                skipped_ignored += 1
                continue
            if _is_inside_any(path, category_roots):
                skipped_category += 1
                continue
            stat = path.stat()
            if db_manager.register_file(path.name, str(path.resolve()), "", 0, stat.st_mtime, is_directory=True):
                detected_dirs += 1
                bus.publish(FileEvent(
                    state=FileState.DETECTED,
                    filepath=str(path.resolve()),
                    filename=path.name,
                ))
        except OSError as exc:
            logger.warning("No se pudo registrar carpeta %s: %s", path, exc)

    logger.info(
        "Escaneo en %s: %s archivos + %s carpetas registrados | %s en categorias existentes | %s ignorados | %s muy recientes (se reintentan)",
        watch_path, detected, detected_dirs, skipped_category, skipped_ignored, skipped_recent,
    )
    return detected + detected_dirs
