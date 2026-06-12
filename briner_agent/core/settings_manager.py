import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MONITORING = {
    "mode": "interval",
    "workspace_dir": "./workspace",
    "poll_interval": 3600,
    "dry_run": False,
    "recursive": False,
}


def validate_poll_interval(value) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("poll_interval debe ser un entero.") from exc

    if interval < 10:
        raise ValueError("poll_interval debe ser mayor o igual a 10 segundos.")
    return interval


def dangerous_workspace_reason(path: str | Path) -> str | None:
    """Explica por que una carpeta NO debe usarse como workspace, o None si es segura.

    NAP mueve archivos dentro del workspace; elegir una carpeta del sistema o el
    perfil completo del usuario reorganizaria archivos criticos.
    """
    resolved = Path(path).expanduser().resolve()

    # Las carpetas temporales (tests, pruebas manuales) son seguras aunque en
    # Windows vivan bajo LOCALAPPDATA.
    import tempfile
    temp_root = Path(tempfile.gettempdir()).resolve()
    if resolved == temp_root or temp_root in resolved.parents:
        return None

    if resolved == Path(resolved.anchor):
        return "es la raiz de la unidad: NAP reorganizaria el disco completo"

    try:
        home = Path.home().resolve()
    except RuntimeError:
        home = None
    if home is not None and resolved == home:
        return "es la carpeta raiz de tu perfil de usuario; elige una subcarpeta (ej. Descargas)"

    protected_env = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "APPDATA", "LOCALAPPDATA")
    for env_name in protected_env:
        value = os.environ.get(env_name)
        if not value:
            continue
        protected = Path(value).resolve()
        if resolved == protected or protected in resolved.parents:
            return f"esta dentro de una carpeta del sistema ({protected})"
        if resolved in protected.parents:
            return f"contiene carpetas del sistema ({protected})"
    return None


def validate_watch_directory(path_value: str | Path) -> str:
    path = Path(path_value).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"La carpeta monitoreada no existe o no es directorio: {path}")
    danger = dangerous_workspace_reason(path)
    if danger:
        raise ValueError(f"Carpeta no permitida: {path} {danger}.")
    return str(path.resolve())


def normalize_monitoring_config(config: dict) -> dict:
    monitoring = {**DEFAULT_MONITORING, **config.get("monitoring", {})}
    mode = str(monitoring.get("mode", "interval")).casefold()
    if mode not in {"interval", "realtime"}:
        logger.warning("Modo de monitoreo invalido '%s'. Usando interval.", mode)
        mode = "interval"
    monitoring["mode"] = mode

    try:
        monitoring["poll_interval"] = validate_poll_interval(monitoring.get("poll_interval", 120))
    except ValueError as exc:
        logger.warning("%s Usando 3600 segundos.", exc)
        monitoring["poll_interval"] = 3600

    monitoring["dry_run"] = bool(monitoring.get("dry_run", config.get("app", {}).get("dry_run", False)))
    config["monitoring"] = monitoring
    return config


def load_user_settings(settings_path: str | Path) -> dict:
    path = Path(settings_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            return json.load(file) or {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("No se pudo leer settings de usuario en %s: %s", path, exc)
        return {}


def save_user_settings(settings_path: str | Path, settings: dict) -> bool:
    path = Path(settings_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(settings, file, indent=2, sort_keys=True)
        return True
    except OSError as exc:
        logger.error("No se pudo guardar settings de usuario en %s: %s", path, exc)
        return False


def create_settings_for_dir(folder: str, settings_path: str | Path) -> dict:
    workspace_dir = validate_watch_directory(folder)
    settings = {
        "monitoring": {
            "mode": "interval",
            "workspace_dir": workspace_dir,
            "poll_interval": 3600,
            "dry_run": False,
        }
    }
    save_user_settings(settings_path, settings)
    print(f"\n  Carpeta configurada: {workspace_dir}")
    return settings


def prompt_for_initial_settings(settings_path: str | Path) -> dict:
    print("\n=== NAP Files-Sorter - Configuracion inicial ===\n")
    print("NAP Files-Sorter organizara automaticamente los archivos de la carpeta que elijas.")
    print("El escaneo se realizara cada hora. Solo necesitas indicar la carpeta.\n")
    while True:
        folder = input("Carpeta a organizar (ej. C:\\Users\\tu_usuario\\Downloads): ").strip().strip('"')
        try:
            workspace_dir = validate_watch_directory(folder)
            break
        except ValueError as exc:
            print(f"  Error: {exc}")
            print("  Asegurate de que la carpeta existe e ingresa la ruta completa.\n")

    settings = {
        "monitoring": {
            "mode": "interval",
            "workspace_dir": workspace_dir,
            "poll_interval": 3600,
            "dry_run": False,
        }
    }
    save_user_settings(settings_path, settings)
    print(f"\n  Carpeta configurada: {workspace_dir}")
    return settings


def merge_settings(config: dict, user_settings: dict) -> dict:
    merged = dict(config)
    for section, values in user_settings.items():
        if isinstance(values, dict):
            merged.setdefault(section, {})
            merged[section].update(values)
        else:
            merged[section] = values
    return normalize_monitoring_config(merged)


def load_or_create_user_settings(
    config: dict,
    settings_path: str | Path,
    prompt_if_missing: bool = True,
    default_dir: str | None = None,
) -> dict:
    user_settings = load_user_settings(settings_path)
    if not user_settings and prompt_if_missing:
        if default_dir:
            try:
                user_settings = create_settings_for_dir(default_dir, settings_path)
            except ValueError as exc:
                # Carpeta inexistente o peligrosa: explicar y dar la oportunidad
                # de elegir otra en vez de abortar con traceback.
                print(f"\n  Error con la carpeta indicada: {exc}\n")
                default_dir = None
        if not user_settings:
            try:
                user_settings = prompt_for_initial_settings(settings_path)
            except (EOFError, KeyboardInterrupt, RuntimeError, OSError):
                logger.warning("No se pudo mostrar el wizard interactivo. Usando defaults seguros.")
    return merge_settings(config, user_settings)
