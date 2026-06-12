import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

CODE_DIR = Path(__file__).resolve().parent
IS_FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else CODE_DIR
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", CODE_DIR)).resolve()


def get_app_data_dir(*, is_frozen: bool, home: Path | None = None) -> Path:
    """Return a writable app-data directory for NAP Files-Sorter on any OS."""
    home_dir = Path.home() if home is None else Path(home)
    override = os.environ.get("NAP_HOME")
    if override:
        if override == "~" or override.startswith("~/") or override.startswith("~\\"):
            return (home_dir / override[2:]).resolve()
        return Path(override).expanduser().resolve()

    if is_frozen:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return (Path(appdata) / "NAP Files-Sorter").resolve()
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            return (Path(xdg_data) / "NAP Files-Sorter").resolve()
        return (home_dir / ".local" / "share" / "NAP Files-Sorter").resolve()

    return CODE_DIR


# State files (settings, db, logs) go to per-user app data when frozen so both exes share them.
APPDATA_DIR = get_app_data_dir(is_frozen=IS_FROZEN)
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from core.settings_manager import (
    load_or_create_user_settings,
    load_user_settings,
    normalize_monitoring_config,
    save_user_settings,
    validate_watch_directory,
)
from db.database_manager import DatabaseManager
from modules.periodic_scanner import scan_directory_once
from runtime.commands import iter_pending_commands, mark_command_done
from runtime.single_instance import SingleInstanceLock
from version import APP_NAME, __version__

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

LOG_DIR = APPDATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        # Rotacion: el servicio corre 24/7; sin tope el log creceria sin limite.
        RotatingFileHandler(
            LOG_DIR / "nap.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("NAPMain")


def load_environment(force: bool = False):
    env_logger = logging.getLogger("NAPMain")
    # When frozen, check APPDATA first (shared between NAPSorter and NAPBackground), then next to the exe.
    env_paths = [APPDATA_DIR / ".env", APP_DIR / ".env"] if IS_FROZEN else [APP_DIR / ".env"]

    if load_dotenv:
        for ep in env_paths:
            load_dotenv(ep, override=force)
        load_dotenv(override=force)

    if not force and (os.environ.get("GROQ_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        env_logger.info("Credenciales IA cargadas.")
        return

    for env_path in env_paths:
        if not env_path.exists():
            continue
        try:
            found_any = False
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                value = line.strip()
                if not value or value.startswith("#"):
                    continue
                if "=" in value:
                    key, raw_value = value.split("=", 1)
                    key = key.strip()
                    raw_value = raw_value.strip().strip('"').strip("'")
                    if key in {"GROQ_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"} and raw_value:
                        os.environ[key] = raw_value
                        if key == "GEMINI_API_KEY":
                            os.environ["GOOGLE_API_KEY"] = raw_value
                        found_any = True
                    continue
            if found_any:
                env_logger.info("Credenciales IA cargadas manualmente desde .env en %s.", env_path.parent)
                return
        except OSError as exc:
            env_logger.warning("No se pudo leer .env en %s: %s", env_path, exc)


load_environment()


def load_config(config_path="config.yaml") -> dict:
    path = Path(config_path)
    if not path.exists():
        logger.warning("Archivo de configuracion no encontrado en %s. Usando valores por defecto.", config_path)
        return {}

    with open(path, "r", encoding="utf-8") as file:
        try:
            return yaml.safe_load(file) or {}
        except yaml.YAMLError as exc:
            logger.error("Error parseando el archivo YAML: %s", exc)
            return {}


def resolve_app_path(path_value: str | Path, base_dir: Path = APP_DIR) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _find_background_exe() -> Path | None:
    """Localiza NAPBackground.exe junto a NAPSorter.exe cuando corre como ejecutable."""
    if not IS_FROZEN:
        return None
    candidate = Path(sys.executable).parent.parent / "NAPBackground" / "NAPBackground.exe"
    return candidate if candidate.exists() else None


def _install_startup_shortcut() -> bool:
    """Crea el acceso directo en la carpeta Startup de Windows para NAPBackground.exe."""
    bg_exe = _find_background_exe()
    if not bg_exe:
        logger.warning("NAPBackground.exe no encontrado. Instala el inicio automatico manualmente.")
        return False

    bg_exe_str = str(bg_exe).replace("'", "''")
    working_dir = str(bg_exe.parent).replace("'", "''")
    ps_cmd = (
        "$startup = [Environment]::GetFolderPath('Startup'); "
        "$lnk = Join-Path $startup 'NAP Files-Sorter.lnk'; "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$link = $shell.CreateShortcut($lnk); "
        f"$link.TargetPath = '{bg_exe_str}'; "
        f"$link.WorkingDirectory = '{working_dir}'; "
        "$link.Arguments = '--no-wizard'; "
        "$link.WindowStyle = 7; "
        "$link.Save(); "
        "Write-Output 'SHORTCUT_OK'"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if "SHORTCUT_OK" in result.stdout:
            logger.info("Acceso directo de inicio creado: %s", bg_exe)
            return True
        logger.warning("PowerShell no confirmo el acceso directo. stderr=%s", result.stderr.strip())
        return False
    except Exception as exc:
        logger.error("Error al crear acceso directo de inicio: %s", exc)
        return False


def build_arg_parser():
    parser = argparse.ArgumentParser(description="NAP Files-Sorter file organizer")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--once", action="store_true", help="Escanea y procesa una sola vez.")
    parser.add_argument("--no-scan", action="store_true", help="No registra archivos existentes al arrancar.")
    parser.add_argument("--dry-run", action="store_true", help="Propone movimientos sin tocar archivos.")
    parser.add_argument("--metrics", action="store_true", help="Imprime metricas basicas y termina.")
    parser.add_argument("--undo-last", action="store_true", help="Deshace el ultimo movimiento real registrado.")
    parser.add_argument("--setup", action="store_true", help="Fuerza el wizard de configuracion inicial.")
    parser.add_argument("--no-wizard", action="store_true", help="Usa config/defaults sin pedir settings iniciales.")
    parser.add_argument("--watch-dir", default=None, help="Carpeta a monitorear (omite el wizard interactivo).")
    parser.add_argument("--api-key", default=None, help="API key de Google Gemini a guardar en APPDATA.")
    return parser


class RuntimeCommandProcessor:
    """Consumes UI commands written by NAPMonitor/Tray without requiring restarts."""

    def __init__(
        self,
        *,
        appdata_dir: Path,
        settings_path: Path,
        config: dict,
        db_manager,
        orchestrator,
        workspace_dir: Path,
        reset_llm_callback,
    ):
        self.appdata_dir = Path(appdata_dir)
        self.settings_path = Path(settings_path)
        self.config = config
        self.db = db_manager
        self.orchestrator = orchestrator
        self.workspace_dir = Path(workspace_dir).resolve()
        self.reset_llm_callback = reset_llm_callback
        self.paused = False

    def _notify(self, tray, title: str, message: str):
        logger.info("%s: %s", title, message)
        if tray and hasattr(tray, "_notify"):
            tray._notify(title, message)

    def _set_workspace(self, raw_path: str, tray=None) -> bool:
        workspace = Path(validate_watch_directory(raw_path)).resolve()
        user_settings = load_user_settings(self.settings_path)
        monitoring = user_settings.setdefault("monitoring", {})
        monitoring["workspace_dir"] = str(workspace)
        monitoring.setdefault("mode", self.config.get("monitoring", {}).get("mode", "interval"))
        monitoring.setdefault("poll_interval", self.config.get("monitoring", {}).get("poll_interval", 3600))
        monitoring.setdefault("dry_run", self.config.get("monitoring", {}).get("dry_run", False))
        if not save_user_settings(self.settings_path, user_settings):
            raise RuntimeError("No se pudo guardar la nueva carpeta monitoreada.")

        self.workspace_dir = workspace
        self.config.setdefault("monitoring", {})["workspace_dir"] = str(workspace)
        self.orchestrator.workspace_root = workspace
        self.orchestrator.config.setdefault("monitoring", {})["workspace_dir"] = str(workspace)
        self.db.cleanup_pending_outside_scan_scope(
            workspace,
            recursive=self.config.get("monitoring", {}).get("recursive", False),
        )
        if tray and hasattr(tray, "workspace_dir"):
            tray.workspace_dir = workspace
        self.paused = False
        return True

    def process_pending(self, tray=None) -> dict:
        result = {"force_scan": False, "workspace_changed": False}
        for path, command in iter_pending_commands(self.appdata_dir):
            try:
                command_type = command.get("type")
                payload = command.get("payload") or {}
                if command_type == "force_scan":
                    result["force_scan"] = True
                elif command_type == "reload_api_key":
                    load_environment(force=True)
                    self.reset_llm_callback()
                    if tray and hasattr(tray, "clear_error"):
                        tray.clear_error()
                    self._notify(tray, "NAP Files-Sorter", "API key actualizada.")
                    result["force_scan"] = True
                elif command_type == "change_workspace":
                    self._set_workspace(payload.get("workspace_dir", ""), tray=tray)
                    self._notify(tray, "NAP Files-Sorter", f"Carpeta monitoreada actualizada: {self.workspace_dir}")
                    result["workspace_changed"] = True
                    result["force_scan"] = True
                elif command_type == "pause":
                    self.paused = True
                    self._notify(tray, "NAP Files-Sorter", "Organizacion pausada.")
                elif command_type == "resume":
                    self.paused = False
                    self._notify(tray, "NAP Files-Sorter", "Organizacion reanudada.")
                    result["force_scan"] = True
                elif command_type == "undo_last":
                    from modules.history import undo_last_move

                    message = undo_last_move(self.db, self.workspace_dir, dry_run=self.config.get("monitoring", {}).get("dry_run", False))
                    self._notify(tray, "NAP Files-Sorter - Deshacer", message)
                    result["force_scan"] = True
                else:
                    logger.warning("Comando desconocido ignorado: %s", command_type)
            except Exception as exc:
                logger.exception("Error procesando comando %s: %s", command.get("type"), exc)
                if tray and hasattr(tray, "set_error"):
                    tray.set_error(f"Error procesando comando {command.get('type')}: {exc}", notify=True)
            finally:
                mark_command_done(path)
        return result


def _run_interval_loop(orchestrator, db_manager, workspace_dir: Path, config: dict, poll_interval: int, once: bool, stop_event=None, force_scan_event=None, tray=None, command_processor: RuntimeCommandProcessor | None = None):
    logger.info("Modo interval: ejecutando escaneo inicial inmediato.")
    processed_total = 0
    errors_total = 0
    needs_scan = True  # skip directory scan in catch-up mode (pending files already in DB)
    result = {}
    while True:
        if stop_event and stop_event.is_set():
            break

        if command_processor:
            command_result = command_processor.process_pending(tray)
            workspace_dir = command_processor.workspace_dir
            if command_result.get("workspace_changed"):
                needs_scan = True
            if command_processor.paused:
                if tray:
                    tray.update_stats(status="Pausado", pending=0, processed_total=processed_total, errors_total=errors_total)
                time.sleep(1)
                continue

        if needs_scan:
            if tray:
                tray.update_stats(status="Escaneando...", pending=0, processed_total=processed_total, errors_total=errors_total, processing=True)
            try:
                detected = scan_directory_once(workspace_dir, db_manager, config)
            except Exception as exc:
                detected = 0
                logger.exception("Error en escaneo de directorio: %s", exc)
        else:
            detected = 0

        if tray:
            tray.update_stats(status="Procesando...", pending=result.get("pending", 0), processed_total=processed_total, errors_total=errors_total, processing=True)
        try:
            if tray:
                result = orchestrator.process_pending_files(
                    tray=tray,
                    base_processed_total=processed_total,
                    base_errors_total=errors_total,
                )
            else:
                result = orchestrator.process_pending_files()
            processed_total += result.get("processed", 0)
            errors_total += result.get("errors", 0)
            last_cycle = datetime.now().strftime("%H:%M:%S")
            logger.info(
                "Ciclo interval terminado. detectados=%s procesados=%s errores=%s",
                detected,
                result.get("processed", 0),
                result.get("errors", 0),
            )
            if tray:
                tray.update_stats(
                    status="Corriendo",
                    pending=result.get("pending", 0),
                    processed_total=processed_total,
                    errors_total=errors_total,
                    last_cycle=last_cycle,
                )
        except Exception as exc:
            logger.exception("Error inesperado en ciclo interval; el servicio continuara: %s", exc)
            if tray:
                tray.update_stats(
                    status="Error en ciclo",
                    processed_total=processed_total,
                    errors_total=errors_total,
                    error=True,
                    error_message=str(exc),
                )

        if once:
            return

        # Catch-up mode: skip poll_interval sleep while files remain pending.
        has_pending = bool(db_manager.get_pending_files(limit=1))
        if has_pending:
            needs_scan = False
            pending_count = result.get("pending", "?")
            logger.info("Modo ponerse al dia: %s archivos pendientes. Procesando siguiente lote sin espera.", pending_count)
            if tray:
                tray.update_stats(
                    status=f"Poniendo al dia ({pending_count} pend.)...",
                    pending=pending_count,
                    processed_total=processed_total,
                    errors_total=errors_total,
                )
            # Brief pause so Groq rate-limit window can partially recover
            _catchup_deadline = time.monotonic() + 3
            _sentinel = APPDATA_DIR / ".force_scan"
            while time.monotonic() < _catchup_deadline:
                if stop_event and (stop_event.is_set() or (force_scan_event and force_scan_event.is_set())):
                    break
                if command_processor:
                    command_result = command_processor.process_pending(tray)
                    workspace_dir = command_processor.workspace_dir
                    if command_result.get("force_scan") or command_result.get("workspace_changed") or command_processor.paused:
                        needs_scan = True
                        break
                if _sentinel.exists():
                    try:
                        _sentinel.unlink()
                    except OSError:
                        pass
                    break
                time.sleep(1)
        else:
            needs_scan = True
            logger.info("Modo interval: al dia. Esperando %s segundo(s) para el siguiente escaneo.", poll_interval)
            if tray:
                tray.update_stats(status="Esperando...", pending=0, processed_total=processed_total, errors_total=errors_total)
            deadline = time.monotonic() + poll_interval
            _sentinel = APPDATA_DIR / ".force_scan"
            while time.monotonic() < deadline:
                if stop_event and (stop_event.is_set() or (force_scan_event and force_scan_event.is_set())):
                    break
                if command_processor:
                    command_result = command_processor.process_pending(tray)
                    workspace_dir = command_processor.workspace_dir
                    if command_result.get("force_scan") or command_result.get("workspace_changed") or command_processor.paused:
                        break
                if _sentinel.exists():
                    try:
                        _sentinel.unlink()
                    except OSError:
                        pass
                    logger.info("Escaneo forzado recibido desde NAPMonitor.")
                    break
                time.sleep(1)
            if force_scan_event:
                force_scan_event.clear()
            if stop_event and stop_event.is_set():
                break


def _run_realtime_loop(orchestrator, db_manager, workspace_dir: Path, config: dict, once: bool, stop_event=None, tray=None, command_processor: RuntimeCommandProcessor | None = None):
    from modules.file_watcher import DirectoryMonitor

    monitor = DirectoryMonitor(watch_directory=str(workspace_dir), db_manager=db_manager, config=config)
    if not config.get("runtime", {}).get("no_scan", False):
        monitor.scan_existing_files()

    if once:
        orchestrator.process_pending_files()
        return

    processed_total = 0
    errors_total = 0
    monitor.start()
    if tray:
        tray.update_stats(status="Corriendo (tiempo real)", processed_total=0, errors_total=0)
    _rt_sentinel = APPDATA_DIR / ".force_scan"
    try:
        while not (stop_event and stop_event.is_set()):
            if command_processor:
                command_result = command_processor.process_pending(tray)
                if command_result.get("workspace_changed"):
                    monitor.stop()
                    workspace_dir = command_processor.workspace_dir
                    monitor = DirectoryMonitor(watch_directory=str(workspace_dir), db_manager=db_manager, config=config)
                    monitor.scan_existing_files()
                    monitor.start()
                if command_result.get("force_scan"):
                    scan_directory_once(command_processor.workspace_dir, db_manager, config)
                if command_processor.paused:
                    if tray:
                        tray.update_stats(status="Pausado", processed_total=processed_total, errors_total=errors_total)
                    time.sleep(1)
                    continue
            if _rt_sentinel.exists():
                try:
                    _rt_sentinel.unlink()
                except OSError:
                    pass
                logger.info("Escaneo forzado recibido desde NAPMonitor (realtime).")
                scan_directory_once(workspace_dir, db_manager, config)
            try:
                if tray:
                    result = orchestrator.process_pending_files(
                        tray=tray,
                        base_processed_total=processed_total,
                        base_errors_total=errors_total,
                    )
                else:
                    result = orchestrator.process_pending_files()
                processed_total += result.get("processed", 0)
                errors_total += result.get("errors", 0)
                if result.get("processed", 0) > 0:
                    last_cycle = datetime.now().strftime("%H:%M:%S")
                    logger.info(
                        "Ciclo realtime terminado. procesados=%s errores=%s",
                        result.get("processed", 0),
                        result.get("errors", 0),
                    )
                    if tray:
                        tray.update_stats(
                            status="Corriendo (tiempo real)",
                            pending=result.get("pending", 0),
                            processed_total=processed_total,
                            errors_total=errors_total,
                            last_cycle=last_cycle,
                        )
            except Exception as exc:
                logger.exception("Error inesperado en ciclo realtime; el servicio continuara: %s", exc)
                if tray:
                    tray.update_stats(
                        status="Error en ciclo",
                        processed_total=processed_total,
                        errors_total=errors_total,
                        error=True,
                        error_message=str(exc),
                    )
            if stop_event:
                stop_event.wait(timeout=3)
            else:
                time.sleep(3)
    finally:
        monitor.stop()


def _run_startup_checks(workspace_dir: Path, orchestrator, tray=None) -> bool:
    errors = []

    if not workspace_dir.exists():
        errors.append(f"La carpeta monitoreada no existe: {workspace_dir}")
    elif not workspace_dir.is_dir():
        errors.append(f"La ruta monitoreada no es una carpeta: {workspace_dir}")

    has_api_key = bool(os.environ.get("GROQ_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
    if not has_api_key:
        logger.warning(
            "Falta GROQ_API_KEY. LLM se inicializara al primer archivo ambiguo."
        )
        if tray and hasattr(tray, "set_error"):
            tray.set_error("Falta API key de Groq. Configura GROQ_API_KEY desde el Monitor", notify=False)

    if errors:
        message = " | ".join(errors)
        logger.error("Verificacion de arranque fallida: %s", message)
        if tray and hasattr(tray, "set_error"):
            tray.set_error(message, notify=True)
        return False

    logger.info("Verificacion de arranque completada. LLM: lazy (primer archivo ambiguo).")
    return True


def main():
    _t0_main = time.perf_counter()
    args = build_arg_parser().parse_args()
    logger.info("Iniciando %s v%s - Agente Autonomo de Gestion de Archivos", APP_NAME, __version__)

    config_path = APP_DIR / "config.yaml"
    if not config_path.exists():
        config_path = RESOURCE_DIR / "config.yaml"
    config = normalize_monitoring_config(load_config(config_path))

    # Settings path: shared via APPDATA when frozen so both exes see the same config.
    settings_path = APPDATA_DIR / "user_settings.json"
    settings_existed = settings_path.exists()
    if args.setup and settings_existed:
        settings_path.unlink()
        settings_existed = False

    config = load_or_create_user_settings(
        config,
        settings_path,
        prompt_if_missing=not args.no_wizard,
        default_dir=getattr(args, 'watch_dir', None),
    )

    # Guardar API key en APPDATA si fue provista via --api-key
    if args.api_key:
        env_path = APPDATA_DIR / ".env"
        env_path.write_text(f"GOOGLE_API_KEY={args.api_key}\n", encoding="utf-8")
        os.environ["GOOGLE_API_KEY"] = args.api_key
        logger.info("API key guardada en %s", env_path)

    # On first-time setup (frozen exe), auto-install Windows startup shortcut and exit.
    if IS_FROZEN and not settings_existed and settings_path.exists():
        logger.info("Primera configuracion detectada. Instalando inicio automatico...")
        ok = _install_startup_shortcut()
        if ok:
            print("\n  Inicio automatico instalado. NAP Files-Sorter se ejecutara al iniciar Windows.")
        else:
            print("\n  No se pudo instalar el inicio automatico.")
            print("  Puedes hacerlo manualmente ejecutando:")
            print("    nap_agent\\scripts\\install_startup.bat")
        if args.setup:
            print("\nConfiguracion completada. Puedes cerrar esta ventana.\n")
            return

    if args.dry_run:
        config.setdefault("monitoring", {})["dry_run"] = True
    if args.no_scan:
        config.setdefault("runtime", {})["no_scan"] = True

    monitoring = config.get("monitoring", {})
    workspace_dir = resolve_app_path(monitoring.get("workspace_dir", "./workspace"))
    # DB path: APPDATA when frozen (shared), local db folder when running as script.
    if IS_FROZEN:
        db_path = APPDATA_DIR / "nap.db"
    else:
        db_path = resolve_app_path(config.get("database", {}).get("sqlite_path", "./db/nap.db"))
    monitoring["workspace_dir"] = str(workspace_dir)
    poll_interval = monitoring.get("poll_interval", 120)
    mode = monitoring.get("mode", "interval")
    dry_run = monitoring.get("dry_run", False)

    logger.info("Modo activo: %s", mode)
    logger.info("Carpeta monitoreada: %s", workspace_dir)
    logger.info("Intervalo efectivo: %s segundo(s)", poll_interval)
    logger.info("Dry-run: %s", dry_run)

    db_manager = DatabaseManager(str(db_path))
    ignored_filenames = {
        name.casefold()
        for name in monitoring.get("ignored_filenames", [".keep", "desktop.ini"])
    }
    db_manager.cleanup_missing_or_ignored(ignored_filenames)
    db_manager.cleanup_pending_outside_scan_scope(
        workspace_dir,
        recursive=monitoring.get("recursive", False),
    )

    if args.metrics:
        from infra.metrics import metrics
        combined = {**db_manager.get_metrics(), "runtime_metrics": metrics.snapshot()}
        print(json.dumps(combined, indent=2, sort_keys=True))
        return

    if args.undo_last:
        from modules.history import undo_last_move

        print(undo_last_move(db_manager, workspace_dir, dry_run=dry_run))
        return

    from core.agent_orchestrator import NAPOrchestrator
    from infra.metrics import M_STARTUP_LATENCY, metrics

    orchestrator = NAPOrchestrator(config=config, db_manager=db_manager, workspace_dir=workspace_dir)
    _startup_latency = time.perf_counter() - _t0_main
    metrics.record(M_STARTUP_LATENCY, _startup_latency)
    logger.info("=== NAP Files-Sorter configuracion activa ===")
    logger.info("Workspace: %s | Existe: %s", workspace_dir, workspace_dir.exists())
    logger.info("Dry-run: %s | Modo: %s | Recursivo: %s", dry_run, mode, monitoring.get("recursive", False))
    logger.info("LLM: lazy (se inicializara al primer archivo ambiguo)")
    logger.info("Startup latency (orquestador listo): %.1f ms", _startup_latency * 1000)
    logger.info("=============================================")

    stop_event = threading.Event()
    force_scan_event = threading.Event()

    def _reset_llm():
        with orchestrator._llm_init_lock:
            orchestrator._llm_initialized = False
            orchestrator._llm_obj = None
            orchestrator._groq_llm = None
            orchestrator._gemini_llm = None
            orchestrator.agent = None
        orchestrator._groq_circuit.record_success()
        orchestrator._gemini_circuit.record_success()
        logger.info("Ambos providers LLM reiniciados tras cambio de API key.")

    command_processor = RuntimeCommandProcessor(
        appdata_dir=APPDATA_DIR,
        settings_path=settings_path,
        config=config,
        db_manager=db_manager,
        orchestrator=orchestrator,
        workspace_dir=workspace_dir,
        reset_llm_callback=_reset_llm,
    )

    if args.once:
        # --once: no tray, direct single-pass processing
        if not _run_startup_checks(workspace_dir, orchestrator):
            return
        try:
            if mode == "realtime":
                _run_realtime_loop(orchestrator, db_manager, workspace_dir, config,
                                   once=True, stop_event=stop_event, command_processor=command_processor)
            else:
                _run_interval_loop(orchestrator, db_manager, workspace_dir, config,
                                   poll_interval, once=True, stop_event=stop_event,
                                   force_scan_event=force_scan_event, command_processor=command_processor)
        except KeyboardInterrupt:
            logger.info("NAP Files-Sorter se ha detenido correctamente por orden del usuario.")
        return

    # Background (continuous) mode —
    # Solo una instancia puede organizar la carpeta: dos procesos compartiendo la
    # misma DB duplicarian movimientos. El SO libera el lock si el proceso muere.
    instance_lock = SingleInstanceLock(APPDATA_DIR / ".nap.lock")
    if not instance_lock.acquire():
        logger.error("Otra instancia de NAP Files-Sorter ya esta corriendo. Este proceso terminara.")
        print("\n  NAP Files-Sorter ya esta en ejecucion (busca el icono en la bandeja del sistema).")
        print("  No es necesario abrirlo de nuevo.\n")
        return

    # pystray.Icon.run() MUST execute on the main thread on Windows (frozen, console=False).
    tray = None
    try:
        from modules.tray_icon import NAPTrayIcon

        tray = NAPTrayIcon(
            workspace_dir=workspace_dir,
            appdata_dir=APPDATA_DIR,
            stop_event=stop_event,
            force_scan_event=force_scan_event,
            on_api_key_changed=_reset_llm,
        )
        orchestrator.set_tray(tray)
    except Exception as exc:
        logger.warning("No se pudo crear el icono de bandeja del sistema: %s", exc)
        tray = None

    startup_ok = _run_startup_checks(workspace_dir, orchestrator, tray=tray)
    if not startup_ok and not workspace_dir.exists():
        command_processor.paused = True
        logger.warning("Procesamiento pausado hasta que el usuario configure una carpeta valida.")

    def _bg_loop():
        try:
            if mode == "realtime":
                _run_realtime_loop(orchestrator, db_manager, workspace_dir, config,
                                   once=False, stop_event=stop_event, tray=tray,
                                   command_processor=command_processor)
            else:
                _run_interval_loop(orchestrator, db_manager, workspace_dir, config,
                                   poll_interval, once=False, stop_event=stop_event,
                                   force_scan_event=force_scan_event, tray=tray,
                                   command_processor=command_processor)
        except Exception as exc:
            logger.exception("Error fatal en loop de procesamiento: %s", exc)
            stop_event.set()

    bg = threading.Thread(target=_bg_loop, daemon=True, name="NAPLoop")
    bg.start()

    if tray:
        try:
            tray.run_main_thread()  # Blocks on main thread until stop() is called
        except Exception as exc:
            logger.error("Error en el icono de bandeja del sistema: %s", exc)
        finally:
            stop_event.set()
            tray.stop()
    else:
        # No tray: run until bg thread exits or KeyboardInterrupt
        try:
            bg.join()
        except KeyboardInterrupt:
            logger.info("NAP Files-Sorter se ha detenido correctamente por orden del usuario.")
            stop_event.set()


if __name__ == "__main__":
    main()
