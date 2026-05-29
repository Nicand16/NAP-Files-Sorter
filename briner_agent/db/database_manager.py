import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Gestiona la conexion y operaciones CRUD para la base de datos de NAP Files-Sorter."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize_db(self):
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            logger.error("No se encontro el archivo de esquema en: %s", schema_path)
            return

        with open(schema_path, "r", encoding="utf-8") as f:
            schema_script = f.read()

        try:
            with self._get_connection() as conn:
                conn.executescript(schema_script)
                self._ensure_retry_count_column(conn)
            logger.info("Base de datos inicializada/verificada exitosamente en: %s", self.db_path.name)
        except sqlite3.Error as e:
            logger.error("Error al inicializar la base de datos: %s", e)

    def _ensure_retry_count_column(self, conn):
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(files)").fetchall()
        }
        if "retry_count" not in columns:
            conn.execute("ALTER TABLE files ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
            logger.info("Migracion aplicada: columna files.retry_count agregada.")
        if "is_directory" not in columns:
            conn.execute("ALTER TABLE files ADD COLUMN is_directory INTEGER DEFAULT 0")
            logger.info("Migracion aplicada: columna files.is_directory agregada.")

    def register_file(
        self,
        filename: str,
        filepath: str,
        extension: str,
        size_bytes: int,
        last_modified: float,
        is_directory: bool = False,
    ):
        """Registra un archivo o carpeta, o actualiza su informacion sin revivir errores terminales."""
        query = """
        INSERT INTO files (filename, filepath, extension, size_bytes, last_modified, is_directory)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(filepath) DO UPDATE SET
            filename=excluded.filename,
            extension=excluded.extension,
            size_bytes=excluded.size_bytes,
            last_modified=excluded.last_modified,
            is_directory=excluded.is_directory,
            status=CASE
                WHEN files.status = 'error' AND COALESCE(files.retry_count, 0) >= 3 THEN files.status
                ELSE 'pending'
            END,
            retry_count=COALESCE(files.retry_count, 0)
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                existing = conn.execute(
                    "SELECT status, retry_count FROM files WHERE filepath = ?",
                    (filepath,),
                ).fetchone()
                terminal_error = (
                    existing is not None
                    and existing["status"] == "error"
                    and int(existing["retry_count"] or 0) >= 3
                )
                conn.execute(query, (filename, filepath, extension, size_bytes, last_modified, int(is_directory)))
                if terminal_error:
                    logger.warning(
                        "Elemento en error definitivo no se reencola (retry_count=%s): %s",
                        existing["retry_count"],
                        filepath,
                    )
                return not terminal_error
        except sqlite3.Error as e:
            logger.error("Error al registrar elemento %s: %s", filepath, e)
            return False

    def remove_file(self, filepath: str):
        """Elimina el registro de un archivo si fue borrado del sistema."""
        query = "DELETE FROM files WHERE filepath = ?"
        try:
            with self._get_connection() as conn:
                conn.execute(query, (filepath,))
                return True
        except sqlite3.Error as e:
            logger.error("Error al eliminar archivo %s: %s", filepath, e)
            return False

    def cleanup_missing_or_ignored(self, ignored_filenames: set[str] | None = None):
        """Elimina de la BD archivos inexistentes o marcadores ignorados."""
        ignored = ignored_filenames or set()
        removed = 0
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT filepath, filename FROM files").fetchall()
                for row in rows:
                    filepath = row["filepath"]
                    filename = row["filename"].casefold()
                    if filename in ignored or not Path(filepath).exists():
                        conn.execute("DELETE FROM files WHERE filepath = ?", (filepath,))
                        removed += 1
            if removed:
                logger.info("Limpieza de BD: %s registro(s) obsoleto(s) removido(s).", removed)
            return removed
        except sqlite3.Error as e:
            logger.error("Error durante limpieza de BD: %s", e)
            return 0

    def cleanup_pending_outside_scan_scope(self, workspace_dir: str | Path, recursive: bool):
        """Elimina pendientes que ya no pertenecen al alcance de escaneo actual."""
        if recursive:
            return 0

        workspace = Path(workspace_dir).resolve()
        removed = 0
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT filepath FROM files WHERE status = 'pending'").fetchall()
                for row in rows:
                    path = Path(row["filepath"]).resolve()
                    if path.parent != workspace:
                        conn.execute("DELETE FROM files WHERE filepath = ?", (row["filepath"],))
                        removed += 1
            if removed:
                logger.info("Limpieza de alcance: %s pendiente(s) fuera de carpeta raiz removido(s).", removed)
            return removed
        except sqlite3.Error as e:
            logger.error("Error durante limpieza de alcance: %s", e)
            return 0

    def update_file_status(self, filepath: str, status: str):
        """Actualiza el estado de procesamiento del archivo."""
        if status == "error":
            query = """
            UPDATE files
            SET status = ?, retry_count = COALESCE(retry_count, 0) + 1
            WHERE filepath = ?
            """
        else:
            query = """
            UPDATE files
            SET status = ?, retry_count = 0
            WHERE filepath = ?
            """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (status, filepath))
                return True
        except sqlite3.Error as e:
            logger.error("Error al actualizar estado de %s: %s", filepath, e)
            return False

    def update_file_path(self, old_path: str, new_path: str, status: str = "processed"):
        """Actualiza la ruta registrada despues de un movimiento exitoso."""
        new = Path(new_path)
        query = """
        UPDATE files
        SET filename = ?, filepath = ?, extension = ?, status = ?, retry_count = 0
        WHERE filepath = ?
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (new.name, str(new.resolve()), new.suffix, status, old_path))
                return True
        except sqlite3.Error as e:
            logger.error("Error al actualizar ruta %s -> %s: %s", old_path, new_path, e)
            return False

    def log_action(self, filepath: str, action_type: str, description: str):
        """Registra una accion que el agente haya tomado sobre un archivo."""
        query = """
        INSERT INTO actions_log (file_id, action_type, description)
        SELECT id, ?, ? FROM files WHERE filepath = ?
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query, (action_type, description, filepath))
                return True
        except sqlite3.Error as e:
            logger.error("Error al registrar accion para el archivo %s: %s", filepath, e)
            return False

    def log_classification_event(
        self,
        filepath: str,
        decision_source: str,
        action: str,
        old_path: str | None = None,
        new_path: str | None = None,
        category: str | None = None,
        reason: str | None = None,
        confidence: float | None = None,
        dry_run: bool = False,
    ):
        """Registra una decision de clasificacion con trazabilidad completa."""
        query = """
        INSERT INTO classification_events (
            file_id,
            decision_source,
            action,
            old_path,
            new_path,
            category,
            reason,
            confidence,
            dry_run
        )
        SELECT id, ?, ?, ?, ?, ?, ?, ?, ? FROM files WHERE filepath = ?
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    query,
                    (
                        decision_source,
                        action,
                        old_path,
                        new_path,
                        category,
                        reason,
                        confidence,
                        int(dry_run),
                        filepath,
                    ),
                )
                return True
        except sqlite3.Error as e:
            logger.error("Error al registrar evento de clasificacion para %s: %s", filepath, e)
            return False

    def get_pending_files(self, limit: int | None = None):
        """Obtiene la lista de archivos y carpetas marcados como pending."""
        query = "SELECT id, filename, filepath, extension, COALESCE(is_directory, 0) as is_directory FROM files WHERE status = 'pending' ORDER BY id"
        params = ()
        if limit:
            query += " LIMIT ?"
            params = (limit,)
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error("Error al obtener elementos pendientes: %s", e)
            return []

    def get_metrics(self):
        """Retorna metricas basicas de estado y decisiones."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                by_status = {
                    row["status"]: row["count"]
                    for row in conn.execute("SELECT status, COUNT(*) AS count FROM files GROUP BY status")
                }
                by_source = {
                    row["decision_source"]: row["count"]
                    for row in conn.execute(
                        "SELECT decision_source, COUNT(*) AS count FROM classification_events GROUP BY decision_source"
                    )
                }
                return {
                    "files_by_status": by_status,
                    "classification_events_by_source": by_source,
                    "total_files": sum(by_status.values()),
                    "total_classification_events": sum(by_source.values()),
                }
        except sqlite3.Error as e:
            logger.error("Error al obtener metricas: %s", e)
            return {}

    def get_recent_classification_decisions(self, limit: int = 200):
        """Return recent successful move decisions to warm the in-memory decision cache."""
        query = """
        SELECT f.filename, f.extension, ce.category, ce.decision_source
        FROM classification_events ce
        JOIN files f ON f.id = ce.file_id
        WHERE ce.action = 'move'
          AND ce.category IS NOT NULL
          AND ce.category != ''
          AND COALESCE(ce.dry_run, 0) = 0
          AND ce.decision_source IN ('rule', 'metadata_rule', 'llm_individual', 'llm_batch')
        ORDER BY ce.timestamp DESC, ce.id DESC
        LIMIT ?
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                return [dict(row) for row in conn.execute(query, (limit,)).fetchall()]
        except sqlite3.Error as e:
            logger.error("Error al cargar decisiones recientes: %s", e)
            return []

    def get_last_move_event(self):
        """Obtiene el ultimo movimiento real registrado para soportar undo."""
        query = """
        SELECT id, old_path, new_path
        FROM classification_events
        WHERE action = 'move'
          AND dry_run = 0
          AND old_path IS NOT NULL
          AND new_path IS NOT NULL
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(query).fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error("Error al obtener ultimo movimiento: %s", e)
            return None

    def log_system_event(self, event_type: str, payload_json: str):
        """Registra eventos de sistema (circuit_open, circuit_recovered) en classification_events con file_id=NULL."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT INTO classification_events (file_id, action, decision_source, reason)
                       VALUES (NULL, ?, 'system', ?)""",
                    (event_type, payload_json),
                )
        except sqlite3.Error as e:
            logger.error("Error al registrar evento de sistema: %s", e)
