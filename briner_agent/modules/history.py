from pathlib import Path

from modules.crud_executor import move_file_secure


def undo_last_move(db_manager, workspace_root: str | Path, dry_run: bool = False) -> str:
    event = db_manager.get_last_move_event()
    if not event:
        return "No hay movimientos reales para deshacer."

    old_path = Path(event["old_path"]).resolve()
    new_path = Path(event["new_path"]).resolve()
    if not new_path.exists():
        return f"No se puede deshacer: el archivo actual no existe en {new_path}"

    workspace = Path(workspace_root).resolve()
    destination = old_path.parent.resolve().relative_to(workspace)
    result = move_file_secure(str(new_path), str(destination), workspace, dry_run=dry_run)
    if not result["ok"]:
        return result["message"]

    db_manager.log_classification_event(
        filepath=event["new_path"],
        decision_source="system",
        action="undo",
        old_path=result.get("old_path"),
        new_path=result.get("new_path"),
        category=str(destination),
        reason=f"Undo del evento {event['id']}.",
        confidence=1.0,
        dry_run=result.get("dry_run", False),
    )
    if not result.get("dry_run"):
        db_manager.update_file_path(event["new_path"], result["new_path"], "processed")
    return result["message"]
