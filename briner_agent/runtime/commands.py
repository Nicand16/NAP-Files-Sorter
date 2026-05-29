import json
import time
import uuid
from pathlib import Path


COMMANDS_DIRNAME = "commands"


def commands_dir(appdata_dir: str | Path) -> Path:
    return Path(appdata_dir) / COMMANDS_DIRNAME


def enqueue_command(appdata_dir: str | Path, command_type: str, payload: dict | None = None) -> Path:
    """Write a small command file for the background process to consume."""
    folder = commands_dir(appdata_dir)
    folder.mkdir(parents=True, exist_ok=True)
    command_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
    final_path = folder / f"{command_id}.json"
    temp_path = folder / f".{command_id}.tmp"
    data = {
        "id": command_id,
        "type": command_type,
        "payload": payload or {},
        "created_at": time.time(),
    }
    temp_path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temp_path.replace(final_path)
    return final_path


def iter_pending_commands(appdata_dir: str | Path) -> list[tuple[Path, dict]]:
    folder = commands_dir(appdata_dir)
    if not folder.exists():
        return []

    commands = []
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("type"):
            commands.append((path, payload))
    return commands


def mark_command_done(path: str | Path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
