"""
briner_agent/monitor.py

Ventana de monitoreo de NAP Files-Sorter (NAPMonitor.exe).

Lee la base SQLite compartida en modo solo lectura y muestra la actividad en
tiempo real con una interfaz moderna: encabezado con estado, tarjetas de
metricas, barra de acciones, busqueda en vivo y tabla de eventos.
Minimizar oculta la ventana a la bandeja del sistema; las acciones se envian a
NAPBackground mediante la cola de comandos (commands/*.json).
"""

import datetime as _dt
import json
import os
import sqlite3
import sys
import threading
import tkinter as tk
import tkinter.filedialog
import tkinter.messagebox
import tkinter.simpledialog
from pathlib import Path
from tkinter import ttk

import pystray
from PIL import ImageTk

# --- Path resolution (mirrors main.py logic) ---
CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from branding import ACCENT, AMBER, GREEN, RED, make_logo_image
from core.settings_manager import validate_watch_directory
from runtime.commands import enqueue_command
from version import APP_NAME, __version__

IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    _appdata = os.environ.get("APPDATA", "")
    _app_dir = (
        Path(_appdata) / "NAP Files-Sorter"
        if _appdata
        else Path.home() / "AppData" / "Roaming" / "NAP Files-Sorter"
    )
else:
    # Dev mode: APPDATA_DIR == CODE_DIR == briner_agent/ (mirrors main.py)
    _app_dir = CODE_DIR

DB_PATH = _app_dir / "nap.db" if IS_FROZEN else _app_dir / "db" / "nap.db"
SETTINGS_PATH = _app_dir / "user_settings.json"
LOGS_DIR = _app_dir / "logs"

_SENTINEL = _app_dir / ".force_scan"  # inter-process signal file

# --- Paleta (hex derivada de branding) ---


def _hex(rgb: tuple) -> str:
    return "#%02x%02x%02x" % rgb


C_BG = "#eef2f7"        # fondo general
C_CARD = "#ffffff"      # tarjetas y tabla
C_HEADER = "#1e293b"    # barra superior
C_HEADER_TEXT = "#f8fafc"
C_HEADER_MUTED = "#94a3b8"
C_TEXT = "#0f172a"
C_MUTED = "#64748b"
C_STRIPE = "#f6f8fb"
C_ACCENT = _hex(ACCENT)
C_GREEN = _hex(GREEN)
C_RED = _hex(RED)
C_AMBER = _hex(AMBER)

_QUERY_EVENTS = """
SELECT
    strftime('%H:%M:%S', ce.timestamp, 'localtime')  AS hora,
    COALESCE(f.filename, ce.old_path)   AS archivo,
    COALESCE(ce.category, ce.action)    AS categoria,
    COALESCE(ce.decision_source, '')    AS fuente,
    ce.action                           AS accion,
    COALESCE(ce.reason, '')             AS razon
FROM classification_events ce
LEFT JOIN files f ON f.id = ce.file_id
WHERE ce.file_id IS NOT NULL
ORDER BY ce.timestamp DESC
LIMIT 100
"""

_QUERY_SYSTEM_EVENT = """
SELECT action, reason, timestamp
FROM classification_events
WHERE action IN ('circuit_open', 'circuit_recovered')
  AND file_id IS NULL
ORDER BY timestamp DESC
LIMIT 1
"""

_QUERY_COUNTS = "SELECT status, COUNT(*) FROM files GROUP BY status"

_REFRESH_MS = 3000

_SOURCE_LABELS = {
    "rule": "Regla",
    "metadata_rule": "Metadatos",
    "cache": "Cache",
    "llm_batch": "IA (lote)",
    "llm_individual": "IA",
    "llm": "IA",
    "system": "Sistema",
}


def _read_workspace() -> str:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data.get("monitoring", {}).get("workspace_dir", "")
    except Exception:
        return ""


def _fetch_data():
    if not DB_PATH.exists():
        return None, None, None
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(_QUERY_EVENTS).fetchall()
            counts_raw = con.execute(_QUERY_COUNTS).fetchall()
            sys_event = con.execute(_QUERY_SYSTEM_EVENT).fetchone()
        finally:
            con.close()
        counts = {r[0]: r[1] for r in counts_raw}
        return rows, counts, sys_event
    except Exception:
        return None, None, None


class NAPMonitorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"NAP Monitor · v{__version__}")
        root.minsize(820, 480)
        root.geometry("1020x600")
        root.configure(bg=C_BG)

        # Icono de ventana (misma marca que la bandeja)
        try:
            self._window_icon = ImageTk.PhotoImage(make_logo_image(ACCENT, 32))
            root.iconphoto(True, self._window_icon)
        except Exception:
            self._window_icon = None

        self._tray: pystray.Icon | None = None
        self._paused = False
        self._all_rows: list[dict] = []
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._render_rows())

        self._setup_style()
        root.bind('<Unmap>', self._on_minimize)
        root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------ #
    # Construccion de la interfaz                                         #
    # ------------------------------------------------------------------ #

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            rowheight=26,
            font=("Segoe UI", 9),
            background=C_CARD,
            fieldbackground=C_CARD,
            foreground=C_TEXT,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background="#e2e8f0",
            foreground=C_HEADER,
            relief="flat",
            padding=(8, 6),
        )
        style.map("Treeview.Heading", background=[("active", "#cbd5e1")])
        style.map(
            "Treeview",
            background=[("selected", "#bfdbfe")],
            foreground=[("selected", C_TEXT)],
        )
        style.configure("Toolbar.TButton", font=("Segoe UI", 9), padding=(12, 6))
        style.configure("Toolbar.TMenubutton", font=("Segoe UI", 9), padding=(12, 6))
        style.configure("Search.TEntry", padding=(8, 5))

    def _build_ui(self):
        self._build_header()
        self._build_cards()
        self._build_toolbar()
        self._build_table()
        self._build_footer()

    def _build_header(self):
        header = tk.Frame(self.root, bg=C_HEADER, padx=16, pady=12)
        header.pack(fill=tk.X)

        left = tk.Frame(header, bg=C_HEADER)
        left.pack(side=tk.LEFT)
        tk.Label(
            left, text=APP_NAME, font=("Segoe UI", 14, "bold"),
            bg=C_HEADER, fg=C_HEADER_TEXT,
        ).pack(anchor=tk.W)
        self._workspace_label = tk.Label(
            left, text="", font=("Segoe UI", 9),
            bg=C_HEADER, fg=C_HEADER_MUTED,
        )
        self._workspace_label.pack(anchor=tk.W)

        right = tk.Frame(header, bg=C_HEADER)
        right.pack(side=tk.RIGHT)
        self._status_dot = tk.Label(right, text="●", font=("Segoe UI", 14), bg=C_HEADER, fg=C_HEADER_MUTED)
        self._status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self._status_label = tk.Label(
            right, text="Conectando...", font=("Segoe UI", 10),
            bg=C_HEADER, fg=C_HEADER_TEXT,
        )
        self._status_label.pack(side=tk.LEFT)

    def _make_card(self, parent, caption: str, color: str):
        card = tk.Frame(parent, bg=C_CARD, padx=16, pady=10,
                        highlightbackground="#dbe2ea", highlightthickness=1)
        value = tk.Label(card, text="–", font=("Segoe UI", 17, "bold"), bg=C_CARD, fg=color)
        value.pack(anchor=tk.W)
        tk.Label(card, text=caption, font=("Segoe UI", 9), bg=C_CARD, fg=C_MUTED).pack(anchor=tk.W)
        return card, value

    def _build_cards(self):
        cards = tk.Frame(self.root, bg=C_BG, padx=16, pady=12)
        cards.pack(fill=tk.X)
        for index in range(4):
            cards.columnconfigure(index, weight=1, uniform="cards")

        card, self._card_pending = self._make_card(cards, "Pendientes", C_ACCENT)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        card, self._card_processed = self._make_card(cards, "Procesados", C_GREEN)
        card.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        card, self._card_errors = self._make_card(cards, "Errores", C_RED)
        card.grid(row=0, column=2, sticky="nsew", padx=(0, 10))
        card, self._card_last = self._make_card(cards, "Ultimo evento", C_MUTED)
        card.grid(row=0, column=3, sticky="nsew")

    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=C_BG, padx=16)
        bar.pack(fill=tk.X)

        ttk.Button(bar, text="⚡ Forzar escaneo", style="Toolbar.TButton",
                   command=self._force_scan).pack(side=tk.LEFT, padx=(0, 6))
        self._pause_button = ttk.Button(bar, text="⏸ Pausar", style="Toolbar.TButton",
                                        command=self._toggle_pause)
        self._pause_button.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="↩ Deshacer ultimo", style="Toolbar.TButton",
                   command=self._undo_last).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="📂 Cambiar carpeta", style="Toolbar.TButton",
                   command=self._change_workspace).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="🗂 Revisar 'Varios'", style="Toolbar.TButton",
                   command=self._open_review_folder).pack(side=tk.LEFT)

        config_btn = ttk.Menubutton(bar, text="⚙ Configuracion", style="Toolbar.TMenubutton")
        menu = tk.Menu(config_btn, tearoff=0, font=("Segoe UI", 9))
        menu.add_command(label="🔑 Cambiar API key de Groq", command=self._change_groq_key)
        menu.add_command(label="🔑 Cambiar API key de Gemini (respaldo)", command=self._change_gemini_key)
        menu.add_separator()
        menu.add_command(label="📄 Abrir logs", command=self._open_logs)
        menu.add_command(label="📁 Abrir carpeta de datos", command=self._open_appdata)
        config_btn["menu"] = menu
        config_btn.pack(side=tk.RIGHT)

    def _build_table(self):
        container = tk.Frame(self.root, bg=C_BG, padx=16, pady=10)
        container.pack(fill=tk.BOTH, expand=True)

        search_row = tk.Frame(container, bg=C_BG)
        search_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(search_row, text="Actividad reciente", font=("Segoe UI", 11, "bold"),
                 bg=C_BG, fg=C_TEXT).pack(side=tk.LEFT)
        search = ttk.Entry(search_row, textvariable=self._filter_var,
                           style="Search.TEntry", width=32)
        search.pack(side=tk.RIGHT)
        tk.Label(search_row, text="🔎", font=("Segoe UI", 10), bg=C_BG, fg=C_MUTED).pack(side=tk.RIGHT, padx=(0, 4))

        frame = tk.Frame(container, bg=C_CARD, highlightbackground="#dbe2ea", highlightthickness=1)
        frame.pack(fill=tk.BOTH, expand=True)

        cols = ("hora", "archivo", "categoria", "fuente", "accion", "razon")
        headings = ("Hora", "Archivo", "Categoria", "Fuente", "Accion", "Razon")
        widths = (70, 250, 200, 80, 70, 220)

        self._tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for col, heading, width in zip(cols, headings, widths):
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=width, minwidth=50, anchor=tk.W)

        self._tree.tag_configure("stripe", background=C_STRIPE)
        self._tree.tag_configure("error_row", foreground=C_RED)
        self._tree.tag_configure("undo_row", foreground=C_AMBER)

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)

    def _build_footer(self):
        footer = tk.Frame(self.root, bg="#e2e8f0", padx=12, pady=4)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        self._footer_label = tk.Label(
            footer,
            text=f"{APP_NAME} v{__version__}   ·   DB: {DB_PATH}",
            font=("Segoe UI", 8), bg="#e2e8f0", fg=C_MUTED, anchor=tk.W,
        )
        self._footer_label.pack(side=tk.LEFT)

    # ------------------------------------------------------------------ #
    # Refresco de datos                                                   #
    # ------------------------------------------------------------------ #

    def _refresh(self):
        rows, counts, sys_event = _fetch_data()
        self._update_ui(rows, counts, sys_event)
        self.root.after(_REFRESH_MS, self._refresh)

    def _set_status(self, color: str, text: str):
        self._status_dot.config(fg=color)
        self._status_label.config(text=text)

    def _update_ui(self, rows, counts, sys_event):
        workspace = _read_workspace()
        self._workspace_label.config(text=f"Organizando: {workspace}" if workspace else "Sin carpeta configurada")

        if rows is None:
            if not DB_PATH.exists():
                self._set_status(C_HEADER_MUTED, "NAP Files-Sorter no esta configurado todavia")
                self._all_rows = []
                self._render_rows()
            else:
                # Error transitorio (DB ocupada): conservar lo ultimo mostrado.
                self._set_status(C_AMBER, "No se pudo leer la base de datos (reintentando...)")
            return

        self._all_rows = [dict(row) for row in rows]
        self._render_rows()

        pending = counts.get("pending", 0)
        processed = counts.get("processed", 0)
        errors = counts.get("error", 0)
        self._card_pending.config(text=f"{pending:,}")
        self._card_processed.config(text=f"{processed:,}")
        self._card_errors.config(text=f"{errors:,}")
        self._card_last.config(text=rows[0]["hora"] if rows else "—")

        now_label = _dt.datetime.now().strftime("%H:%M:%S")
        self._footer_label.config(
            text=f"{APP_NAME} v{__version__}   ·   DB: {DB_PATH}   ·   Actualizado: {now_label}"
        )

        # --- Circuit breaker state (from system events in DB) ---
        circuit_active = False
        circuit_msg = ""
        circuit_is_ratelimit = False
        if sys_event and sys_event["action"] == "circuit_open":
            try:
                payload = json.loads(sys_event["reason"])
                etype = payload.get("type", "unknown")
                provider = str(payload.get("provider", "LLM")).capitalize()
                recovery_s = float(payload.get("recovery_seconds", 60))
                # SQLite CURRENT_TIMESTAMP es UTC: comparar contra UTC para no
                # inflar el tiempo restante con el offset de la zona horaria local.
                event_ts = _dt.datetime.fromisoformat(sys_event["timestamp"]).replace(tzinfo=_dt.timezone.utc)
                elapsed = (_dt.datetime.now(_dt.timezone.utc) - event_ts).total_seconds()
                remaining = max(0, recovery_s - elapsed)
                if remaining > 0:
                    circuit_active = True
                    if etype == "rate_limit":
                        circuit_is_ratelimit = True
                        circuit_msg = f"Cuota de {provider} excedida — reintento automatico en ~{int(remaining)}s"
                    else:
                        circuit_msg = f"API key de {provider} invalida — cambiala en ⚙ Configuracion"
            except Exception:
                pass

        has_errors = any(row["accion"] == "error" for row in self._all_rows)
        error_reasons = [row["razon"] for row in self._all_rows if row["accion"] == "error" and row["razon"]]

        if circuit_active and circuit_is_ratelimit:
            self._set_status(C_AMBER, circuit_msg)
        elif circuit_active:
            self._set_status(C_RED, circuit_msg)
        elif has_errors or errors > 0:
            detail = f": {error_reasons[0][:70]}" if error_reasons else ""
            self._set_status(C_RED, f"Hay errores{detail}")
        elif rows:
            self._set_status(C_GREEN, "Activo")
        else:
            self._set_status(C_HEADER_MUTED, "Sin actividad reciente")

    def _render_rows(self):
        for item in self._tree.get_children():
            self._tree.delete(item)

        needle = self._filter_var.get().strip().casefold()
        visible_index = 0
        for row in self._all_rows:
            values = (
                row.get("hora") or "",
                row.get("archivo") or "",
                row.get("categoria") or "",
                _SOURCE_LABELS.get(row.get("fuente") or "", row.get("fuente") or ""),
                row.get("accion") or "",
                row.get("razon") or "",
            )
            if needle and not any(needle in str(value).casefold() for value in values):
                continue
            tags = []
            if visible_index % 2 == 1:
                tags.append("stripe")
            if row.get("accion") == "error":
                tags.append("error_row")
            elif row.get("accion") == "undo":
                tags.append("undo_row")
            self._tree.insert("", tk.END, values=values, tags=tuple(tags))
            visible_index += 1

    # ------------------------------------------------------------------ #
    # Acciones                                                            #
    # ------------------------------------------------------------------ #

    def _force_scan(self):
        try:
            enqueue_command(_app_dir, "force_scan")
            _SENTINEL.touch()
            self._set_status(C_ACCENT, "Escaneo solicitado — procesara en breve")
        except Exception as exc:
            tkinter.messagebox.showerror("NAP Monitor", f"No se pudo solicitar escaneo:\n{exc}")

    def _change_workspace(self):
        folder = tkinter.filedialog.askdirectory(
            title="Selecciona la carpeta que NAP Files-Sorter debe organizar"
        )
        if not folder:
            return
        try:
            validated = validate_watch_directory(folder)
        except ValueError as exc:
            tkinter.messagebox.showerror("Carpeta no valida", str(exc))
            return
        confirmed = tkinter.messagebox.askyesno(
            "Confirmar carpeta",
            "NAP Files-Sorter organizara TODOS los archivos sueltos de:\n\n"
            f"{validated}\n\n"
            "Los movera a subcarpetas por categoria dentro de esa misma carpeta.\n"
            "¿Quieres continuar?",
        )
        if not confirmed:
            return
        try:
            enqueue_command(_app_dir, "change_workspace", {"workspace_dir": validated})
            self._set_status(C_ACCENT, "Cambio de carpeta solicitado — se aplicara en breve")
        except Exception as exc:
            tkinter.messagebox.showerror("NAP Monitor", f"No se pudo cambiar la carpeta:\n{exc}")

    def _toggle_pause(self):
        self._paused = not self._paused
        command = "pause" if self._paused else "resume"
        try:
            enqueue_command(_app_dir, command)
            self._pause_button.config(text="▶ Reanudar" if self._paused else "⏸ Pausar")
            self._set_status(
                C_AMBER if self._paused else C_GREEN,
                "Organizacion pausada" if self._paused else "Organizacion reanudada",
            )
        except Exception as exc:
            self._paused = not self._paused
            tkinter.messagebox.showerror("NAP Monitor", f"No se pudo enviar el comando:\n{exc}")

    def _undo_last(self):
        try:
            enqueue_command(_app_dir, "undo_last")
            self._set_status(C_ACCENT, "Deshacer solicitado — se aplicara en breve")
        except Exception as exc:
            tkinter.messagebox.showerror("NAP Monitor", f"No se pudo solicitar deshacer:\n{exc}")

    def _open_review_folder(self):
        workspace = _read_workspace()
        if not workspace:
            tkinter.messagebox.showinfo("NAP Monitor", "Primero configura una carpeta monitoreada.")
            return
        root = Path(workspace)
        candidates = [
            root / "7. Varios" / "Documentos por Revisar",
            root / "Varios" / "Documentos por Revisar",
        ]
        target = next((path for path in candidates if path.exists()), candidates[0])
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
        except Exception as exc:
            tkinter.messagebox.showerror("NAP Monitor", f"No se pudo abrir la carpeta de revision:\n{exc}")

    # ------------------------------------------------------------------ #
    # API keys                                                            #
    # ------------------------------------------------------------------ #

    def _update_env_key(self, env_path: Path, key_name: str, value: str):
        lines = []
        updated = False
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                if line.strip().startswith(key_name + "="):
                    lines.append(f"{key_name}={value}")
                    updated = True
                else:
                    lines.append(line)
        if not updated:
            lines.append(f"{key_name}={value}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _ask_api_key(self, title: str, prompt: str) -> str:
        value = tkinter.simpledialog.askstring(title, prompt, parent=self.root)
        if value is None:
            return ""
        return value.strip().strip('"').strip("'")

    def _save_api_key(self, key_name: str, new_key: str, expected_prefix: str, provider: str) -> bool:
        if not new_key:
            return False
        if len(new_key) < 20 or " " in new_key:
            tkinter.messagebox.showerror(
                "API key no valida",
                f"La API key de {provider} ingresada no parece valida "
                "(muy corta o contiene espacios). Revisa que la copiaste completa.",
            )
            return False
        if expected_prefix and not new_key.startswith(expected_prefix):
            proceed = tkinter.messagebox.askyesno(
                "Formato inesperado",
                f"Las API keys de {provider} normalmente empiezan con '{expected_prefix}'.\n"
                "La que ingresaste no coincide.\n\n¿Guardarla de todas formas?",
            )
            if not proceed:
                return False
        try:
            self._update_env_key(_app_dir / ".env", key_name, new_key)
            enqueue_command(_app_dir, "reload_api_key")
            return True
        except Exception as exc:
            tkinter.messagebox.showerror("NAP Monitor", f"No se pudo guardar la API key:\n{exc}")
            return False

    def _change_groq_key(self):
        new_key = self._ask_api_key(
            "API key de Groq",
            "Pega tu API key de Groq (console.groq.com):",
        )
        if self._save_api_key("GROQ_API_KEY", new_key, "gsk_", "Groq"):
            self._set_status(C_GREEN, "API key de Groq guardada — se recargara automaticamente")

    def _change_gemini_key(self):
        new_key = self._ask_api_key(
            "API key de Gemini (opcional)",
            "Pega tu API key de Gemini (aistudio.google.com/apikey).\n"
            "Es opcional: se usa como respaldo automatico de Groq.",
        )
        if self._save_api_key("GOOGLE_API_KEY", new_key, "AIza", "Gemini"):
            self._set_status(C_GREEN, "API key de Gemini guardada — se usara como respaldo")

    def _open_logs(self):
        if LOGS_DIR.exists():
            os.startfile(str(LOGS_DIR))
        else:
            tkinter.messagebox.showinfo(
                "NAP Monitor", f"Carpeta de logs no encontrada:\n{LOGS_DIR}"
            )

    def _open_appdata(self):
        if _app_dir.exists():
            os.startfile(str(_app_dir))

    # --- System tray on minimize ---

    def _on_minimize(self, event: tk.Event):
        if event.widget is self.root:
            self.root.after(1, self._hide_to_tray)

    def _hide_to_tray(self):
        self.root.withdraw()
        if self._tray is None:
            menu = pystray.Menu(
                pystray.MenuItem("Mostrar NAP Monitor", self._restore, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Cerrar", self._on_close),
            )
            self._tray = pystray.Icon("NAPMonitor", make_logo_image(ACCENT), "NAP Monitor", menu)
            threading.Thread(target=self._tray.run, daemon=True, name="MonitorTray").start()

    def _restore(self, icon=None, item=None):
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.root.after_idle(self._do_restore)

    def _do_restore(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_close(self, icon=None, item=None):
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.root.after_idle(self.root.destroy)


def main():
    root = tk.Tk()
    NAPMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
