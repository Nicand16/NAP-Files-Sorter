import logging
import os
import shutil
import threading
import time
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

INVALID_FOLDER_CHARS = set('<>:"\\|?*')
_thread_local = threading.local()


def _resolve_inside_workspace(path: Path, workspace_root: Path) -> bool:
    try:
        path.resolve().relative_to(workspace_root.resolve())
        return True
    except ValueError:
        return False


def _workspace_mismatch_message(source_path, source_resolved: Path, workspace_root, workspace_resolved: Path) -> str:
    try:
        common = os.path.commonpath([str(source_resolved), str(workspace_resolved)])
    except ValueError as exc:
        common = f"<sin commonpath: {exc}>"
    return (
        "Error: El archivo origen esta fuera del workspace. "
        f"source_original='{source_path}' | source_resuelto='{source_resolved}' | "
        f"workspace_original='{workspace_root}' | workspace_resuelto='{workspace_resolved}' | "
        f"commonpath='{common}'"
    )


def normalize_destination(destination_folder_name: str, destination_aliases: dict | None = None) -> str:
    destination_aliases = destination_aliases or {}
    normalized = destination_folder_name.replace("\\", "/").strip("/")
    for logical_root, real_root in destination_aliases.items():
        if normalized == logical_root:
            return real_root
        if normalized.startswith(f"{logical_root}/"):
            return f"{real_root}/{normalized[len(logical_root) + 1:]}"
    return destination_folder_name


def _validate_destination(destination_folder_name: str) -> Path:
    if destination_folder_name in ("", "."):
        return Path(".")

    destination = Path(destination_folder_name)
    if destination.is_absolute():
        raise ValueError("El destino no puede ser una ruta absoluta.")

    parts = destination.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("El destino contiene segmentos no permitidos.")

    for part in parts:
        if any(char in INVALID_FOLDER_CHARS for char in part):
            raise ValueError(f"El segmento de destino '{part}' contiene caracteres invalidos.")

    return destination


def _unique_destination(dest_path: Path) -> Path:
    if not dest_path.exists():
        return dest_path

    stem = dest_path.stem
    suffix = dest_path.suffix
    parent = dest_path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def move_file_secure(
    source_path: str,
    destination_folder_name: str,
    workspace_root: str | Path,
    dry_run: bool = False,
    destination_aliases: dict | None = None,
) -> dict:
    """Move a file into a validated folder under workspace_root."""
    try:
        src = Path(source_path)
        if not src.exists():
            return {"ok": False, "message": f"Error: El archivo {source_path} no existe en el disco."}
        if not src.is_file():
            return {"ok": False, "message": "Error: La ruta origen no es un archivo."}

        workspace = Path(workspace_root).resolve()
        src_resolved = src.resolve()
        if not _resolve_inside_workspace(src, workspace):
            message = _workspace_mismatch_message(source_path, src_resolved, workspace_root, workspace)
            logger.error(message)
            return {"ok": False, "error_code": "workspace_mismatch", "message": message}

        destination_folder_name = normalize_destination(destination_folder_name, destination_aliases)
        safe_destination = _validate_destination(destination_folder_name)
        dest_dir = (workspace / safe_destination).resolve()
        if not _resolve_inside_workspace(dest_dir, workspace):
            return {
                "ok": False,
                "error_code": "destination_outside_workspace",
                "message": (
                    "Error: El destino resuelto sale del workspace. "
                    f"destino_resuelto='{dest_dir}' | workspace_resuelto='{workspace}'"
                ),
            }

        dest_path = _unique_destination(dest_dir / src.name)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "old_path": str(src_resolved),
                "new_path": str(dest_path),
                "message": f"Dry-run: {src.name} se moveria a {dest_path}",
            }

        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest_path))
        logger.info("Accion (move_file): %s movido a %s/", src.name, destination_folder_name)
        return {
            "ok": True,
            "dry_run": False,
            "old_path": str(src_resolved),
            "new_path": str(dest_path.resolve()),
            "message": f"Exito: Archivo reubicado exitosamente a la ruta {dest_path}",
        }
    except ValueError as e:
        return {"ok": False, "message": f"Error de validacion al mover archivo: {str(e)}"}
    except Exception as e:
        logger.error("Error en move_file: %s", e)
        return {"ok": False, "message": f"Error critico al mover archivo: {str(e)}"}


def _record_thread_move(result: dict):
    moves = getattr(_thread_local, "moves", None)
    if moves is None:
        moves = []
        _thread_local.moves = moves
    moves.append(result)


def consume_thread_moves() -> list[dict]:
    moves = list(getattr(_thread_local, "moves", []))
    _thread_local.moves = []
    return moves


def build_move_file_tool(
    workspace_root: str | Path,
    dry_run: bool = False,
    destination_aliases: dict | None = None,
):
    @tool
    def move_file(source_path: str, destination_folder_name: str) -> str:
        """
        Mueve un archivo desde source_path a una carpeta validada bajo el workspace.
        destination_folder_name debe ser una ruta relativa de categoria, nunca absoluta.
        """
        result = move_file_secure(
            source_path,
            destination_folder_name,
            workspace_root,
            dry_run,
            destination_aliases,
        )
        _record_thread_move(result)
        return result["message"]

    return move_file


def move_folder_secure(
    source_path: str,
    destination_folder_name: str,
    workspace_root: str | Path,
    dry_run: bool = False,
    destination_aliases: dict | None = None,
) -> dict:
    """Move a directory into a validated parent folder under workspace_root."""
    try:
        src = Path(source_path)
        if not src.exists():
            return {"ok": False, "message": f"Error: La carpeta {source_path} no existe en el disco."}
        if not src.is_dir():
            return {"ok": False, "message": "Error: La ruta origen no es una carpeta."}

        workspace = Path(workspace_root).resolve()
        src_resolved = src.resolve()
        if not _resolve_inside_workspace(src, workspace):
            message = _workspace_mismatch_message(source_path, src_resolved, workspace_root, workspace)
            logger.error(message)
            return {"ok": False, "error_code": "workspace_mismatch", "message": message}

        destination_folder_name = normalize_destination(destination_folder_name, destination_aliases)
        safe_destination = _validate_destination(destination_folder_name)
        dest_parent = (workspace / safe_destination).resolve()
        if not _resolve_inside_workspace(dest_parent, workspace):
            return {
                "ok": False,
                "error_code": "destination_outside_workspace",
                "message": (
                    f"Error: El destino resuelto sale del workspace. "
                    f"destino_resuelto='{dest_parent}' | workspace_resuelto='{workspace}'"
                ),
            }

        dest_path = dest_parent / src.name
        if dest_path.exists():
            counter = 1
            while dest_path.exists():
                dest_path = dest_parent / f"{src.name} ({counter})"
                counter += 1

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "old_path": str(src_resolved),
                "new_path": str(dest_path),
                "message": f"Dry-run: carpeta '{src.name}' se moveria a {dest_path}",
            }

        dest_parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest_path))
        logger.info("Accion (move_folder): %s movida a %s/", src.name, destination_folder_name)
        return {
            "ok": True,
            "dry_run": False,
            "old_path": str(src_resolved),
            "new_path": str(dest_path.resolve()),
            "message": f"Exito: Carpeta reubicada exitosamente a {dest_path}",
        }
    except ValueError as e:
        return {"ok": False, "message": f"Error de validacion al mover carpeta: {str(e)}"}
    except Exception as e:
        logger.error("Error en move_folder: %s", e)
        return {"ok": False, "message": f"Error critico al mover carpeta: {str(e)}"}


def quarantine_file_secure(
    file_path: str,
    workspace_root: str | Path,
    dry_run: bool = False,
) -> dict:
    """Move a file to a quarantine folder under the workspace instead of deleting it."""
    try:
        path = Path(file_path)
        if not path.exists():
            return {"ok": False, "message": f"Error: El archivo {file_path} no existe en el disco."}
        if not path.is_file():
            return {"ok": False, "message": "Error: La ruta origen no es un archivo."}

        workspace = Path(workspace_root).resolve()
        source_resolved = path.resolve()
        if not _resolve_inside_workspace(source_resolved, workspace):
            message = _workspace_mismatch_message(file_path, source_resolved, workspace_root, workspace)
            logger.error(message)
            return {"ok": False, "error_code": "workspace_mismatch", "message": message}

        quarantine_dir = workspace / "_NAP Quarantine" / time.strftime("%Y-%m")
        dest_path = _unique_destination(quarantine_dir / path.name)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "old_path": str(source_resolved),
                "new_path": str(dest_path),
                "message": f"Dry-run: {path.name} se moveria a cuarentena en {dest_path}",
            }

        quarantine_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest_path))
        logger.info("Accion (quarantine_file): %s movido a cuarentena.", path.name)
        return {
            "ok": True,
            "dry_run": False,
            "old_path": str(source_resolved),
            "new_path": str(dest_path.resolve()),
            "message": f"Exito: Archivo movido a cuarentena en {dest_path}",
        }
    except Exception as e:
        logger.error("Error en quarantine_file: %s", e)
        return {"ok": False, "message": f"Error al mover a cuarentena: {str(e)}"}


def build_delete_file_tool(
    workspace_root: str | Path = ".",
    dry_run: bool = False,
):
    @tool
    def delete_file(file_path: str) -> str:
        """
        Mueve un archivo basura o temporal a _NAP Quarantine.
        No elimina permanentemente archivos del usuario.
        """
        result = quarantine_file_secure(file_path, workspace_root, dry_run)
        _record_thread_move(result)
        return result["message"]

    return delete_file


def get_crud_tools(
    workspace_root: str | Path = ".",
    dry_run: bool = False,
    destination_aliases: dict | None = None,
):
    """Retorna la lista de herramientas CRUD para LangChain."""
    return [
        build_move_file_tool(workspace_root, dry_run, destination_aliases),
        build_delete_file_tool(workspace_root, dry_run),
    ]
