import logging
import os
import sys
import threading
from pathlib import Path

from runtime.event_bus import FileEvent, FileState, bus
from runtime.commands import enqueue_command

logger = logging.getLogger(__name__)


def _make_icon(color: tuple):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(*color, 255))
    return img


class NAPTrayIcon:
    _GREEN = (34, 197, 94)
    _BLUE = (59, 130, 246)
    _RED = (239, 68, 68)

    def __init__(self, workspace_dir: Path, appdata_dir: Path, stop_event: threading.Event, force_scan_event: threading.Event, on_api_key_changed=None):
        self.workspace_dir = Path(workspace_dir)
        self.appdata_dir = Path(appdata_dir)
        self.stop_event = stop_event
        self.force_scan_event = force_scan_event
        self._on_api_key_changed = on_api_key_changed
        self._icon = None
        self._lock = threading.Lock()
        self._status = "Iniciando..."
        self._color = self._GREEN
        self._pending = 0
        self._processed_total = 0
        self._errors_total = 0
        self._last_cycle = "-"
        self.last_error_message = ""
        self._pending_notifications: list[tuple[str, str]] = []
        self._recent_file_events: list[FileEvent] = []
        self._max_recent_events: int = 5
        self._paused = False
        bus.subscribe(self._on_file_event)

    def update_stats(
        self,
        status: str,
        pending: int = 0,
        processed_total: int = 0,
        errors_total: int = 0,
        last_cycle: str | None = None,
        error: bool = False,
        error_message: str | None = None,
        clear_error: bool = False,
        processing: bool = False,
    ):
        with self._lock:
            self._status = status
            self._pending = pending
            self._processed_total = processed_total
            self._errors_total = errors_total
            if last_cycle is not None:
                self._last_cycle = last_cycle
            if error_message:
                self.last_error_message = error_message
            elif clear_error:
                self.last_error_message = ""
            elif error and not self.last_error_message:
                self.last_error_message = status

            if error or self.last_error_message:
                self._color = self._RED
            elif processing:
                self._color = self._BLUE
            else:
                self._color = self._GREEN
        self._refresh_icon()

    def _on_file_event(self, event: FileEvent):
        with self._lock:
            self._recent_file_events = [e for e in self._recent_file_events if e.filepath != event.filepath]
            self._recent_file_events.insert(0, event)
            self._recent_file_events = self._recent_file_events[:self._max_recent_events]
        self._refresh_icon()

    def set_error(self, message: str, notify: bool = True):
        with self._lock:
            self._status = "Error"
            self.last_error_message = message
            self._color = self._RED
        self._refresh_icon()
        if notify:
            self._notify("NAP Files-Sorter - Error", message)

    def clear_error(self):
        with self._lock:
            self.last_error_message = ""
            self._color = self._GREEN
        self._refresh_icon()

    def _title(self, status: str, error_message: str = "") -> str:
        if error_message:
            return f"NAP - ERROR: {error_message[:96]}"
        return f"NAP - {status}"

    def _refresh_icon(self):
        if self._icon:
            try:
                with self._lock:
                    color = self._color
                    status = self._status
                    error_message = self.last_error_message
                self._icon.icon = _make_icon(color)
                self._icon.title = self._title(status, error_message)
                self._icon.menu = self._build_menu()
                self._icon.update_menu()
            except Exception:
                pass

    def _notify(self, title: str, message: str):
        message = message[:255]  # pystray limit is 256 chars
        if self._icon:
            try:
                self._icon.notify(message, title)
                return
            except Exception as exc:
                logger.warning("No se pudo mostrar notificacion de tray: %s", exc)
                return
        with self._lock:
            self._pending_notifications.append((title, message))

    def _flush_pending_notifications(self, icon):
        with self._lock:
            notifications = list(self._pending_notifications)
            self._pending_notifications.clear()
        for title, message in notifications:
            try:
                icon.notify(message, title)
            except Exception as exc:
                logger.warning("No se pudo mostrar notificacion pendiente de tray: %s", exc)

    def _build_menu(self):
        import pystray
        with self._lock:
            status = self._status
            pending = self._pending
            processed_total = self._processed_total
            errors_total = self._errors_total
            last_cycle = self._last_cycle
            error_message = self.last_error_message
            recent = list(self._recent_file_events)

        items = [
            pystray.MenuItem(f"NAP Files-Sorter - {status}", None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
        if error_message:
            items.extend(
                [
                    pystray.MenuItem(f"Error activo: {error_message[:120]}", None, enabled=False),
                    pystray.Menu.SEPARATOR,
                ]
            )
        items.extend(
            [
                pystray.MenuItem(f"Pendientes: {pending}", None, enabled=False),
                pystray.MenuItem(f"Procesados total: {processed_total}", None, enabled=False),
                pystray.MenuItem(f"Errores total: {errors_total}", None, enabled=False),
                pystray.MenuItem(f"Ultimo ciclo: {last_cycle}", None, enabled=False),
            ]
        )

        if recent:
            _state_icon = {
                FileState.DETECTED:   "?",
                FileState.QUEUED:     "o",
                FileState.PROCESSING: "*",
                FileState.CLASSIFIED: "+",
                FileState.MOVED:      ">",
                FileState.IGNORED:    "-",
                FileState.ERROR:      "!",
            }
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("Recientes:", None, enabled=False))
            for ev in recent:
                icon_char = _state_icon.get(ev.state, ".")
                label = f"  [{icon_char}] {ev.short_label()}"[:80]
                items.append(pystray.MenuItem(label, None, enabled=False))

        items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Abrir monitor en tiempo real", self._open_monitor),
                pystray.MenuItem("Ver logs", self._open_logs),
                pystray.MenuItem("Abrir carpeta monitoreada", self._open_workspace),
                pystray.MenuItem("Abrir documentos por revisar", self._open_review_folder),
                pystray.MenuItem("Forzar escaneo ahora", self._force_scan),
                pystray.MenuItem("Cambiar carpeta...", self._change_workspace),
                pystray.MenuItem("Pausar/Reanudar", self._toggle_pause),
                pystray.MenuItem("Deshacer ultimo movimiento", self._undo_last),
                pystray.MenuItem("Cambiar API key...", self._change_api_key),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Detener NAP Files-Sorter", self._quit),
            ]
        )
        return pystray.Menu(*items)

    def _open_monitor(self, icon, item):
        import subprocess
        # When frozen: NAPMonitor.exe lives next to NAPBackground.exe in dist/
        monitor_exe = Path(sys.executable).parent.parent / "NAPMonitor" / "NAPMonitor.exe"
        if monitor_exe.exists():
            subprocess.Popen([str(monitor_exe)])
            return
        # Dev-mode fallback: run monitor.py with the current Python interpreter
        monitor_script = Path(__file__).resolve().parent.parent / "monitor.py"
        if monitor_script.exists():
            subprocess.Popen([sys.executable, str(monitor_script)])

    def _open_logs(self, icon, item):
        log_dir = self.appdata_dir / "logs"
        log_file = log_dir / "nap.log"
        target = log_file if log_file.exists() else log_dir
        if target.exists():
            os.startfile(str(target))

    def _open_workspace(self, icon, item):
        if self.workspace_dir.exists():
            os.startfile(str(self.workspace_dir))

    def _open_review_folder(self, icon, item):
        candidates = [
            self.workspace_dir / "7. Varios" / "Documentos por Revisar",
            self.workspace_dir / "Varios" / "Documentos por Revisar",
        ]
        target = next((path for path in candidates if path.exists()), candidates[0])
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
        except Exception as exc:
            self._notify("NAP Files-Sorter", f"No se pudo abrir revision: {exc}")

    def _force_scan(self, icon, item):
        enqueue_command(self.appdata_dir, "force_scan")
        self.force_scan_event.set()

    def _change_workspace(self, icon, item):
        import subprocess
        selected_path = str(self.workspace_dir).replace("'", "''")
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = 'Selecciona la carpeta que NAP Files-Sorter debe organizar'; "
            f"$dialog.SelectedPath = '{selected_path}'; "
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
            "Write-Output $dialog.SelectedPath }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True, text=True, timeout=120,
            )
            folder = result.stdout.strip()
        except Exception as exc:
            self._notify("NAP Files-Sorter", f"No se pudo abrir selector de carpeta: {exc}")
            return
        if not folder:
            return
        enqueue_command(self.appdata_dir, "change_workspace", {"workspace_dir": folder})
        self._notify("NAP Files-Sorter", "Cambio de carpeta solicitado.")

    def _toggle_pause(self, icon, item):
        self._paused = not self._paused
        enqueue_command(self.appdata_dir, "pause" if self._paused else "resume")
        self._notify("NAP Files-Sorter", "Organizacion pausada." if self._paused else "Organizacion reanudada.")

    def _undo_last(self, icon, item):
        enqueue_command(self.appdata_dir, "undo_last")
        self._notify("NAP Files-Sorter", "Deshacer solicitado.")

    def _change_api_key(self, icon, item):
        import subprocess
        ps_script = (
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            "$key = [Microsoft.VisualBasic.Interaction]::InputBox("
            "'Pega tu nueva API key de Groq:', "
            "'NAP Files-Sorter - Cambiar API key', ''); "
            "Write-Output $key"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True, text=True, timeout=60,
            )
            new_key = result.stdout.strip()
        except Exception as exc:
            logger.warning("Error mostrando dialogo de API key: %s", exc)
            return
        if not new_key:
            return
        env_path = self.appdata_dir / ".env"
        try:
            lines = []
            updated = False
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                    if line.strip().startswith("GROQ_API_KEY="):
                        lines.append(f"GROQ_API_KEY={new_key}")
                        updated = True
                    else:
                        lines.append(line)
            if not updated:
                lines.append(f"GROQ_API_KEY={new_key}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            enqueue_command(self.appdata_dir, "reload_api_key")
        except Exception as exc:
            logger.error("No se pudo guardar la API key: %s", exc)
            return
        os.environ["GROQ_API_KEY"] = new_key
        if self._on_api_key_changed:
            try:
                self._on_api_key_changed()
            except Exception as exc:
                logger.warning("Error al reiniciar LLM tras cambio de key: %s", exc)
        self.clear_error()
        logger.info("API key de Groq actualizada correctamente.")

    def _quit(self, icon, item):
        self.stop_event.set()
        icon.stop()

    def run_main_thread(self):
        """Run pystray.Icon.run() on the calling thread (must be the main thread on Windows).

        Blocks until stop() is called. Use this instead of start() when running as a frozen
        windowless exe — Win32 requires the message pump on the main thread.
        """
        import pystray
        with self._lock:
            color = self._color
            status = self._status
            error_message = self.last_error_message
        img = _make_icon(color)
        self._icon = pystray.Icon(
            "NAP Files-Sorter",
            img,
            self._title(status, error_message),
            menu=self._build_menu(),
        )
        self._icon.run(setup=self._flush_pending_notifications)

    def start(self):
        t = threading.Thread(target=self._run, daemon=True, name="NAPTray")
        t.start()

    def _run(self):
        try:
            import pystray
            with self._lock:
                status = self._status
                color = self._color
                error_message = self.last_error_message
            img = _make_icon(color)
            self._icon = pystray.Icon(
                "NAP Files-Sorter",
                img,
                self._title(status, error_message),
                menu=self._build_menu(),
            )
            self._icon.run(setup=self._flush_pending_notifications)
        except Exception as exc:
            logger.warning("No se pudo iniciar el icono de bandeja del sistema: %s", exc)

    def stop(self):
        bus.unsubscribe(self._on_file_event)
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
