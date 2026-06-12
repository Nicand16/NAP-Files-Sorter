# NAP Files-Sorter � Arquitectura y gu�a t�cnica

Este documento describe en detalle la l�gica, estructura y decisiones de dise�o de NAP Files-Sorter. Est� pensado como referencia completa para que un desarrollador o un LLM pueda entender qu� hace cada parte del sistema y c�mo modificarla.

---

## Qu� hace NAP Files-Sorter

NAP Files-Sorter es un agente aut�nomo de organizaci�n de archivos para Windows. Monitorea una carpeta configurada por el usuario (t�picamente Descargas), clasifica cada archivo nuevo mediante reglas deterministas y/o IA (Google Gemini), y lo mueve a una subcarpeta de destino seg�n su tipo. Se ejecuta en segundo plano, arranca con Windows, y expone su estado mediante un �cono en la bandeja del sistema y una ventana de monitoreo.

---

## Estado actual v1.0.0

La app esta orientada a usuarios finales: solo deben cambiar la carpeta monitoreada o la API key cuando sea necesario. El resto se maneja desde NAP Monitor o desde el icono de bandeja mediante comandos IPC:

- Cambiar carpeta monitoreada sin editar JSON.
- Cambiar API key y recargar Gemini sin reiniciar.
- Pausar/reanudar procesamiento.
- Forzar escaneo inmediato.
- Deshacer el ultimo movimiento.
- Abrir `7. Varios/Documentos por Revisar`.
- Retirar basura a `_NAP Quarantine` sin borrado permanente.
- Precargar cache de decisiones desde SQLite al arrancar.

---

## Componentes del sistema

El sistema se distribuye como tres ejecutables independientes que comparten un directorio de datos en `%APPDATA%\NAP Files-Sorter\`:

### `NAPSorter.exe` � Consola de configuraci�n y diagn�stico
- Tiene consola visible (stdout/stderr).
- Se usa para: configuraci�n inicial (`--setup`), pasadas manuales (`--once`), diagn�stico (`--metrics`), deshacer �ltimo movimiento (`--undo-last`).
- Comparte exactamente el mismo c�digo fuente (`main.py`) que NAPBackground; la diferencia es solo `console=True` en el spec de PyInstaller.

### `NAPBackground.exe` � Servicio en segundo plano
- Sin consola visible (`console=False`).
- Se lanza con `--no-wizard` para evitar el wizard interactivo.
- Es el proceso que realmente organiza los archivos de forma continua.
- Arranca con Windows mediante un acceso directo en `Startup`.
- Corre el �cono de la bandeja del sistema en el hilo principal (requisito de Win32) y el loop de procesamiento en un hilo daemon.

### `NAPMonitor.exe` � Ventana de monitoreo
- Sin consola visible; interfaz gr�fica Tkinter + pystray.
- Lee la base de datos SQLite compartida en modo solo lectura.
- Muestra los �ltimos 100 eventos de clasificaci�n, contadores y estado.
- Se comunica con NAPBackground mediante un archivo centinela (`.force_scan`) para forzar escaneos.
- Minimizar oculta la ventana a la bandeja del sistema (no a la barra de tareas).
- C�digo fuente: `briner_agent/monitor.py` (archivo independiente, no comparte c�digo con `main.py`).

---

## �rbol de archivos

```
Files Sorter/
+-- Install.bat                          ? Instalador de usuario final
+-- MANUAL_USO.md                        ? Manual de usuario
+-- ARCHITECTURE.md                      ? Este archivo
+-- README.md                            ? Resumen del proyecto
+-- briner_agent/
    +-- main.py                          ? Punto de entrada �nico (NAPSorter.exe y NAPBackground.exe)
    +-- monitor.py                       ? Punto de entrada de NAPMonitor.exe
    +-- version.py                       ? Version unica compartida por los 3 exes
    +-- branding.py                      ? Logo y paleta compartidos (bandeja + Monitor)
    +-- config.yaml                      ? Configuraci�n base + taxonom�a (se empaqueta en el exe)
    +-- requirements.txt
    +-- build_all.bat                    ? Compila los 3 exes con PyInstaller
    +-- NAPSorter.spec                      ? Spec PyInstaller para NAPSorter.exe (console=True)
    +-- NAPBackground.spec            ? Spec PyInstaller para NAPBackground.exe (console=False)
    +-- NAPMonitor.spec               ? Spec PyInstaller para NAPMonitor.exe (console=False)
    +-- rthook_fix_socket.py             ? Runtime hook para socket en exes frozen
    +-- core/
    �   +-- agent_orchestrator.py        ? Pipeline de clasificaci�n 3 fases + circuit breaker
    �   +-- llm_engine.py                ? Inicializaci�n lazy de Gemini via LangChain
    �   +-- settings_manager.py          ? Carga y merge de config.yaml + user_settings.json
    +-- modules/
    �   +-- periodic_scanner.py          ? scan_directory_once(): rglob + registro en DB
    �   +-- file_watcher.py              ? Monitoreo en tiempo real con watchdog (modo realtime)
    �   +-- rules_engine.py              ? Clasificaci�n determinista por extensi�n y keyword
    �   +-- crud_executor.py             ? Movimiento seguro de archivos (resolve colisiones)
    �   +-- tray_icon.py                 ? �cono de bandeja (pystray) + "Cambiar API key"
    �   +-- multimodal_parser.py         ? Extracci�n de texto de PDF/DOCX/XLSX para contexto LLM
    �   +-- history.py                   ? Registro y deshacer �ltimo movimiento
    +-- classifiers/
    �   +-- decision_cache.py            ? Cach� LRU + TTL de decisiones LLM por patr�n de nombre
    +-- runtime/
    �   +-- event_bus.py                 ? Pub/sub de FileEvent (7 estados por archivo)
    �   +-- circuit_breaker.py           ? CLOSED/OPEN/HALF_OPEN para proteger llamadas a Gemini
    �   +-- single_instance.py           ? Candado de instancia unica (bloqueo de archivo del SO)
    +-- infra/
    �   +-- metrics.py                   ? Contadores y timers en proceso (sin dependencias externas)
    +-- db/
    �   +-- database_manager.py          ? CRUD SQLite: files, actions_log, classification_events
    �   +-- schema.sql                   ? Esquema de la base de datos
    +-- scripts/
    �   +-- install_startup.bat          ? Instala acceso directo en Startup de Windows
    +-- tests/
        +-- test_core.py                 ? Reglas, movimientos, DB, config, loop de intervalo
        +-- test_event_bus.py            ? Pub/sub, estados, short_label
        +-- test_circuit_breaker.py      ? Transiciones CLOSED/OPEN/HALF_OPEN
        +-- test_decision_cache.py       ? LRU, TTL, normalizaci�n de d�gitos en nombres
```

---

## Directorio de datos compartido (`%APPDATA%\NAP Files-Sorter\`)

Todos los archivos de estado se guardan aqu�. Tanto `NAPSorter.exe` como `NAPBackground.exe` y `NAPMonitor.exe` apuntan al mismo directorio.

```
%APPDATA%\NAP Files-Sorter\
+-- .env                    ? GOOGLE_API_KEY=...  (cargado con python-dotenv)
+-- user_settings.json      ? Carpeta monitoreada, intervalo, modo, dry_run
+-- nap.db               ? Base de datos SQLite
+-- .force_scan             ? Archivo centinela IPC: NAPMonitor lo crea, NAPBackground lo consume
+-- logs/
    +-- nap.log          ? Log de actividad (INFO+)
```

Desde v1.0.0 tambien existe `%APPDATA%\NAP Files-Sorter\commands\`, una cola de comandos JSON usada por NAPMonitor y la bandeja para pedir acciones al proceso background: cambiar carpeta, recargar API key, pausar/reanudar, forzar escaneo y deshacer ultimo movimiento. `.force_scan` queda como senal simple compatible para escaneos inmediatos.

En modo dev (sin frozen), `APPDATA_DIR` apunta al propio directorio `briner_agent/` y la DB se ubica en `briner_agent/db/nap.db`.

---

## Base de datos SQLite

### Tabla `files`
Registro de cada archivo visto por NAP Files-Sorter.

| Columna | Tipo | Descripci�n |
|---|---|---|
| `id` | INTEGER PK | Autoincremental |
| `filename` | TEXT | Nombre del archivo |
| `filepath` | TEXT UNIQUE | Ruta absoluta |
| `extension` | TEXT | Extensi�n (`.pdf`, `.jpg`...) |
| `size_bytes` | INTEGER | Tama�o en bytes |
| `status` | TEXT | `pending` ? `processed` / `error` |
| `retry_count` | INTEGER | Intentos fallidos |
| `last_modified` | TIMESTAMP | mtime del sistema de archivos |
| `created_at` | TIMESTAMP | Cu�ndo se registr� en la DB |

### Tabla `classification_events`
Auditor�a de cada decisi�n de clasificaci�n.

| Columna | Descripci�n |
|---|---|
| `file_id` | FK a `files.id` |
| `decision_source` | `rule` / `llm` / `system` |
| `action` | `move` / `error` / `skip` |
| `old_path` | Ruta original |
| `new_path` | Ruta de destino |
| `category` | Categor�a asignada |
| `reason` | Justificaci�n (texto libre) |
| `confidence` | Confianza 0�1 (solo LLM) |
| `dry_run` | 1 si fue simulaci�n |

### Tabla `actions_log`
Log gen�rico de acciones (usado para auditor�a adicional).

---

## Sistema de configuraci�n

La configuraci�n se construye en dos capas que se fusionan:

### Capa 1: `config.yaml` (base, inmutable para el usuario)
Empaquetado dentro del exe por PyInstaller (`datas=[('config.yaml', '.')]`). Contiene:
- **`monitoring`**: `mode`, `poll_interval`, `recursive: false` (solo archivos en la ra�z de la carpeta configurada, no en subcarpetas), `ignored_patterns`, `destination_aliases` (mapeo de categor�a a nombre de carpeta con n�mero).
- **`processing`**: `max_files_per_cycle` (500), `llm_batch_size` (50), `llm_individual_threshold` (6), `llm_bulk_content_max_chars` (700), `llm_individual_content_max_chars` (3000), `varios_min_confidence` (0.82), `llm_timeout_seconds` (60), `circuit_breaker_threshold` (3), `circuit_breaker_recovery_seconds` (60), `decision_cache_size` (200), `decision_cache_ttl_seconds` (3600).
- **`taxonomy`**: lista de reglas deterministas (categor�a + extensiones / palabras clave).
- **`llm`**: `model` (`gemini-2.5-flash`), `temperature` (0.2).

### Capa 2: `user_settings.json` (carpeta del usuario)
Creado por `--setup` o `Install.bat`. Contiene solo:
```json
{
  "monitoring": {
    "mode": "interval",
    "workspace_dir": "D:\\Descargas",
    "poll_interval": 3600,
    "dry_run": false
  }
}
```

### Fusi�n (`merge_settings` en `settings_manager.py`)
Se hace un merge secci�n a secci�n (shallow update): los valores de `user_settings.json` sobreescriben los de `config.yaml` solo para las claves que aparecen en el JSON. La secci�n `processing`, `taxonomy`, `llm` y `rules` siempre vienen �ntegras de `config.yaml`.

### Ruta de carga en frozen exe
```python
config_path = APP_DIR / "config.yaml"          # junto al exe ? no existe en dist/
if not config_path.exists():
    config_path = RESOURCE_DIR / "config.yaml"  # sys._MEIPASS ? _internal/ ? existe
```

---

## Pipeline de procesamiento

### Modo `interval` (por defecto)

```
_run_interval_loop()
    �
    +- [needs_scan=True] scan_directory_once()
    �       +- rglob workspace/, registra cada archivo como 'pending' en DB
    �          (omite archivos en carpetas de categor�as ya existentes)
    �
    +- orchestrator.process_pending_files()   ? hasta max_files_per_cycle (500)
    �       +- ver pipeline 3 fases abajo
    �
    +- �quedan pendientes en DB?
          S� ? needs_scan=False, esperar 3 s, repetir  (modo catch-up)
          No ? needs_scan=True, esperar poll_interval (3600 s), repetir
```

El modo catch-up (necesario para carpetas con decenas de miles de archivos) evita tanto el escaneo de disco redundante como la espera de 1 hora entre lotes.

### Modo `realtime` (alternativo)

```
_run_realtime_loop()
    �
    +- DirectoryMonitor.scan_existing_files()   ? escaneo inicial
    +- DirectoryMonitor.start()                  ? watchdog observa cambios en tiempo real
    �       +- BrinerEventHandler.on_created()  ? register_file() en DB
    �
    +- loop cada 3 s:
            +- check .force_scan sentinel
            +- orchestrator.process_pending_files()
```

### Pipeline de clasificaci�n 3 fases (`agent_orchestrator.py`)

```
get_pending_files(limit=500)
         �
    [Fase 1: Reglas deterministas]
    Para cada archivo:
         +- classify_file() en rules_engine.py
         �       +- match por extensi�n (config taxonomy)
         �       +- match por keywords en filename (casefold)
         �
         +- �match encontrado?
         �       S� ? move_file_secure() ? status='processed' ? MOVED
         �       No ? archivo pasa a lista `ambiguous`
         �
    [Fase 2: Clasificaci�n LLM por lote]
    Para cada chunk de llm_batch_size (50) archivos ambiguos:
         +- decision_cache.get(extension, filename_pattern)
         �       Hit  ? usar decisi�n cacheada, sin llamada a API
         �       Miss ? continuar
         �
         +- multimodal_parser.extract() para archivos legibles (PDF, DOCX...)
         �       ? hasta 300 chars de contenido por archivo
         �
         +- build_taxonomy_prompt() ? prompt con lista de archivos + contenido parcial
         +- circuit.before_call()   ? lanza CircuitOpenError si OPEN
         +- llm.invoke(prompt)      ? 1 llamada a Gemini para todo el chunk
         +- parsear JSON de respuesta
         +- decision_cache.set() para cada decisi�n nueva
         +- move_file_secure() para cada archivo ? status='processed'
         +- time.sleep(2) entre chunks  ? pace para respetar l�mite 15 req/min Gemini
         �
    [Fase 3: Fallback ReAct por archivo]
    Solo si el lote LLM falla completamente:
         +- Agente LangGraph ReAct, 1 archivo a la vez (�ltimo recurso)
```

En v1.0.0 la fase de ambiguos usa capas adicionales:

- Cache en memoria precargado desde SQLite con decisiones recientes.
- `collect_file_metadata()` extrae tipo MIME, tamano, fechas, metadatos PDF/Office/imagen/ZIP y preview de contenido.
- `classify_file_context()` intenta clasificar localmente con nombre + metadatos + contenido antes de llamar a Gemini.
- Si hay pocos ambiguos (`llm_individual_threshold <= 6`), Gemini se consulta archivo por archivo con contexto amplio.
- Si hay muchos ambiguos, Gemini se consulta en bulk con previews compactos.
- `Varios` no se cachea y los documentos sin evidencia suficiente van a `Varios/Documentos por Revisar`.
- Las herramientas LLM nunca borran permanentemente: `delete_file` mueve a `_NAP Quarantine`.

### Movimiento de archivos (`crud_executor.py`)

- Resuelve el alias de categor�a: `"Multimedia"` ? `"4. Multimedia"` (seg�n `destination_aliases` en config.yaml).
- Construye la ruta de destino: `workspace/4. Multimedia/Imagenes y Capturas/foto.jpg`.
- Si existe un archivo con el mismo nombre, a�ade sufijo num�rico: `foto (1).jpg`.
- Registra el evento en `classification_events`.
- Actualiza `files.status` a `'processed'`.

---

## Comunicaci�n entre procesos (IPC)

### Archivo centinela `.force_scan`
- **Creador:** NAPMonitor (bot�n "? Forzar escaneo") o el �cono de bandeja (opci�n "Forzar escaneo ahora").
- **Consumidor:** NAPBackground, que lo comprueba en cada iteraci�n del sleep loop (cada 1 segundo en modo interval, cada iteraci�n del loop en modo realtime).
- **Efecto:** interrumpe el sleep de `poll_interval` y lanza un ciclo inmediato.
- **Ruta:** `%APPDATA%\NAP Files-Sorter\.force_scan`

### Cola de comandos `commands/*.json`
- **Creador:** NAPMonitor y BrinerTrayIcon mediante `runtime.commands.enqueue_command()`.
- **Consumidor:** `RuntimeCommandProcessor` en `main.py`.
- **Comandos:** `force_scan`, `reload_api_key`, `change_workspace`, `pause`, `resume`, `undo_last`.
- **Efecto:** permite que el usuario final controle NAP Files-Sorter sin reiniciar y sin editar archivos manualmente.
- **Ruta:** `%APPDATA%\NAP Files-Sorter\commands\*.json`

### Base de datos SQLite (compartida)
NAPMonitor accede a la DB en modo solo lectura (`mode=ro` en la URI de conexi�n) para mostrar el estado. NAPBackground tiene acceso de escritura.

---

## Circuit Breaker (`runtime/circuit_breaker.py`)

Protege las llamadas a la API de Gemini contra fallos en cascada.

```
CLOSED --(3 fallos consecutivos)--? OPEN --(60 s)--? HALF_OPEN
                                                           �
                                          �xito del probe --? CLOSED
                                          fallo del probe --? OPEN
```

- **CLOSED:** todas las llamadas LLM pasan normalmente.
- **OPEN:** todas las llamadas LLM son rechazadas con `CircuitOpenError`. Los archivos ambiguos quedan como `pending` (no se marcan como error) y se procesan en el siguiente ciclo cuando el circuit se recupere.
- **HALF_OPEN:** se permite una sola llamada de prueba.

El circuit breaker se resetea (`record_success()`) cuando el usuario cambia la API key desde el men� de la bandeja, permitiendo la recuperaci�n inmediata.

---

## Cach� de decisiones LRU (`classifiers/decision_cache.py`)

Evita llamadas repetidas a la API para archivos con el mismo patr�n de nombre.

- **Clave:** `(extension, patr�n_normalizado)` � los d�gitos del nombre se normalizan a `#` para que `foto_001.jpg` y `foto_002.jpg` compartan la misma entrada.
- **Capacidad:** 200 entradas (LRU � la menos usada se descarta).
- **TTL:** 3600 segundos.
- **Warm start:** al arrancar, el orquestador precarga decisiones recientes desde SQLite (`get_recent_classification_decisions`) y evita repetir llamadas LLM tras reinicios.
- **Efecto:** clasificar 1000 fotos solo requiere 1 llamada LLM si todas tienen el mismo patr�n de nombre.

---

## Event Bus (`runtime/event_bus.py`)

Pub/sub desacoplado para comunicar el estado de cada archivo entre el orchestrator y el �cono de bandeja.

**7 estados posibles:**
```
DETECTED ? QUEUED ? PROCESSING ? CLASSIFIED ? MOVED
                                            ? IGNORED
                                            ? ERROR
```

El �cono de bandeja se suscribe (`bus.subscribe`) y muestra las �ltimas 5 acciones en el men� contextual. Cuando se muestra el �cono, se descarga del bus (`bus.unsubscribe`).

---

## �cono de bandeja (`modules/tray_icon.py`)

- Usa `pystray` para el �cono y men� del sistema.
- En exes frozen con `console=False`, `pystray.Icon.run()` **debe ejecutarse en el hilo principal** (requisito de Win32). Por eso `main.py` llama a `tray.run_main_thread()` desde el hilo principal y lanza el loop de procesamiento en un hilo daemon.
- El m�todo `_change_api_key()` muestra un `InputBox` de VisualBasic via PowerShell (sin dependencias UI propias), guarda la clave en `.env`, la inyecta en `os.environ`, y llama al callback `on_api_key_changed` que resetea el LLM lazy del orchestrator.

---

## LLM � Inicializaci�n lazy (`core/llm_engine.py`)

El modelo Gemini **no se inicializa al arrancar**. Se inicializa en el primer archivo ambiguo que necesite clasificaci�n LLM. Esto permite:
- Bandeja visible en < 2 segundos aunque la API key sea inv�lida.
- Resetear el modelo tras un cambio de API key simplemente poniendo `_llm_initialized = False`.

El orchestrator mantiene:
```python
self._llm_obj = None
self._llm_initialized = False
self._llm_init_lock = threading.Lock()
self.agent = None  # agente ReAct, tambi�n lazy
```

---

## Modo dry-run

Cuando `dry_run=True` en la configuraci�n, NAP Files-Sorter clasifica normalmente pero **no mueve** ning�n archivo. Los eventos se registran en `classification_events` con `dry_run=1`. �til para verificar la taxonom�a antes de aplicarla.

---

## Argumentos de l�nea de comandos (`main.py`)

| Argumento | Descripci�n |
|---|---|
| `--setup` | Reconfigura desde cero (borra user_settings.json y pide datos) |
| `--watch-dir PATH` | Carpeta a monitorear (usada con `--setup`) |
| `--api-key KEY` | Guarda la API key en `.env` |
| `--no-wizard` | No muestra el wizard interactivo (modo servicio) |
| `--once` | Ejecuta un solo ciclo y sale |
| `--dry-run` | No mueve archivos, solo simula |
| `--no-scan` | Salta el escaneo inicial del directorio |
| `--metrics` | Imprime m�tricas y sale |
| `--undo-last` | Deshace el �ltimo movimiento registrado |

---

## Sistema de build (PyInstaller)

Los tres specs de PyInstaller viven en `briner_agent/`:

### `NAPBackground.spec`
- `console=False`
- `datas=[('config.yaml', '.'), ('db/schema.sql', 'db')]` � empaqueta la configuraci�n base y el schema SQL dentro del exe.
- `hiddenimports` incluye: `langchain_google_genai`, `langgraph`, `pystray._win32`, `PIL`, todos los m�dulos locales de `infra`, `runtime`, `classifiers`.
- `runtime_hooks=['rthook_fix_socket.py']` � workaround para socket en exes frozen en Windows.

### `NAPSorter.spec`
- Id�ntico a NAPBackground excepto `console=True`.

### `NAPMonitor.spec`
- `console=False`
- Sin LangChain ni LangGraph (excluidos expl�citamente).
- Solo necesita: `sqlite3`, `tkinter`, `pystray`, `PIL` y `runtime.commands`.

### Comando de build
```powershell
cd briner_agent
python -m PyInstaller --clean --noconfirm NAPSorter.spec
python -m PyInstaller --clean --noconfirm NAPBackground.spec
python -m PyInstaller --clean --noconfirm NAPMonitor.spec
```
O simplemente: `build_all.bat`

### Crear zip de release
```powershell
Compress-Archive -Path "briner_agent\dist\NAP Files-Sorter","briner_agent\dist\NAPBackground","briner_agent\dist\NAPMonitor","Install.bat","README.md","MANUAL_USO.md" -DestinationPath "briner_v1.0.0.zip" -Force
```
El zip coloca las 3 carpetas y `Install.bat` al mismo nivel ra�z.

---

## Tests

```powershell
cd briner_agent
python -m pytest tests/ -q
# Resultado esperado v1.2.0: 69 passed
```

### Cobertura por archivo de test

| Archivo | Qu� prueba |
|---|---|
| `test_core.py` | scan_directory_once (ignora categor�as, ignora patrones), _run_interval_loop (escanea antes de dormir), DatabaseManager, settings merge, arg parser |
| `test_event_bus.py` | pub/sub, m�ltiples suscriptores, desuscripci�n, short_label por estado |
| `test_circuit_breaker.py` | Transiciones CLOSED?OPEN?HALF_OPEN?CLOSED, probe exitoso/fallido |
| `test_decision_cache.py` | LRU eviction, TTL, normalizaci�n de d�gitos en nombres de archivo |
| `test_orchestrator_recovery.py` | Restauraci�n del recovery base de los circuit breakers tras l�mite diario |
| `test_user_safety.py` | Carpetas peligrosas, edad m�nima de archivos, candado de instancia �nica |
| `test_monitor_ui.py` | Smoke test de la UI del Monitor: render, filtro en vivo, validaci�n de API keys |

---

## Flujo de instalaci�n (Install.bat)

1. Verifica existencia de `NAP Files-Sorter\NAPSorter.exe`, `NAP Files-Sorter\_internal\python314.dll`, `NAP Files-Sorter\_internal\_socket.pyd`.
2. Muestra di�logo de selecci�n de carpeta (PowerShell + Windows.Forms en archivo .ps1 temporal).
3. Pide la API key por consola.
4. Crea `%APPDATA%\NAP Files-Sorter\.env` con `GOOGLE_API_KEY=...`.
5. Ejecuta `NAPSorter.exe --setup --watch-dir "..."` ? crea `user_settings.json` + instala acceso directo en Startup.
6. Mata cualquier instancia anterior de `NAPBackground.exe`.
7. Lanza `NAPBackground.exe --no-wizard` en segundo plano.
8. Crea acceso directo "NAP Monitor" en el Escritorio.
9. Lanza `NAPMonitor.exe`.

---

## Decisiones de dise�o relevantes

| Decisi�n | Motivo |
|---|---|
| 3 ejecutables separados | NAPSorter.exe necesita consola para el setup interactivo; NAPBackground necesita `console=False` para no flashear ventanas al arrancar con Windows; NAPMonitor es puro UI sin dependencias de LangChain. |
| LLM inicializaci�n lazy | La bandeja del sistema aparece en < 2 s aunque la API no est� disponible. Un error de API key no impide arrancar. |
| IPC por archivos | `.force_scan` se conserva para escaneo inmediato y `commands/*.json` permite comandos de usuario final sin sockets ni HTTP. Compatible con frozen exes. |
| Catch-up mode | Con carpetas de 70k+ archivos, dormir 1 hora entre lotes tomar�a semanas. El catch-up procesa de forma continua hasta quedar al d�a. |
| 2s entre chunks LLM | La API gratuita de Gemini tiene l�mite de 15 req/min. Sin pausa, 3 fallos consecutivos abren el circuit breaker y bloquean la clasificaci�n el resto del ciclo. |
| Decision cache con normalizaci�n de d�gitos | Fotos de WhatsApp siguen patrones como `IMG_001.jpg`, `IMG_002.jpg`. Normalizar los d�gitos a `#` permite reusar decisiones entre miles de fotos similares. |
| SQLite compartida en APPDATA | NAPSorter.exe, NAPBackground.exe y NAPMonitor.exe deben leer/escribir el mismo estado. APPDATA es el punto com�n entre los 3 procesos independientemente de d�nde est�n instalados. |
