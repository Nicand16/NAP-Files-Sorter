# Changelog

## v1.2.0 — 2026-06-11

### A prueba de errores de usuario

- **Carpetas peligrosas bloqueadas**: ya no se puede configurar como workspace la raiz de una unidad (`C:\`), el perfil de usuario completo, ni carpetas del sistema (`Windows`, `Program Files`, `AppData`). Aplica al instalador, al wizard y al cambio de carpeta en caliente desde Monitor o bandeja. Si la carpeta del instalador es invalida, el setup pide otra en vez de abortar con traceback.
- **Descargas en curso protegidas**: los archivos modificados hace menos de 15 segundos (configurable con `monitoring.min_file_age_seconds`) no se registran todavia, evitando mover descargas o copias a medio escribir. Se agregaron mas patrones de temporales de navegador (`.partial`, `.download`, `.opdownload`).
- **Instancia unica**: NAPBackground usa un candado de archivo a nivel de SO; si ya hay una instancia organizando la carpeta, la segunda se cierra sola con un mensaje claro. El candado se libera automaticamente si el proceso muere.
- **Validacion de API keys**: el Monitor valida formato (Groq `gsk_`, Gemini `AIza`), rechaza claves vacias/cortas/con espacios y pide confirmacion si el formato no coincide. El instalador avisa si la key no parece de Groq y la bandeja rechaza claves obviamente invalidas.
- **Confirmacion al cambiar carpeta**: el Monitor valida la carpeta elegida y muestra un dialogo de confirmacion explicando que se reorganizaran todos los archivos sueltos.

### Interfaz renovada

- **NAP Monitor rediseñado**: encabezado oscuro con marca y estado en color, tarjetas de metricas (pendientes/procesados/errores/ultimo evento), barra de acciones organizada, menu `⚙ Configuracion`, buscador que filtra el historial en vivo, filas rayadas con codigos de color por tipo de evento, y pie con version y ruta de la DB.
- **Logo propio**: icono de carpeta sobre placa de color, compartido entre la bandeja del sistema, la ventana del Monitor y su icono de ventana (`branding.py`). El color del icono de bandeja sigue indicando estado (verde/azul/rojo).
- Los dialogos de API key del Monitor ahora son nativos (Tkinter) en lugar de ventanas PowerShell/VisualBasic.
- Fuentes de decision legibles en el historial ("Regla", "IA (lote)", "Cache"...) en lugar de identificadores internos.

### Otros

- Version unica compartida en `version.py` y nuevo flag `--version`.
- Documentacion actualizada (manual de uso, README, arquitectura).
- 15 tests nuevos: carpetas peligrosas, edad minima de archivos, candado de instancia unica y smoke tests de la UI del Monitor (69 en total).

## v1.1.0 — 2026-06-11

### Corregido

- **Circuit breaker no se recuperaba del limite diario**: cuando Groq o Gemini reportaban limite diario, el tiempo de recuperacion subia a 1 hora y nunca volvia a su valor base (65 s). Despues de un solo error diario, cualquier cuota por minuto posterior bloqueaba el LLM una hora completa. Ahora el tiempo base se restaura en cada exito y en errores de cuota por minuto.
- **Horas incorrectas en NAP Monitor**: SQLite guarda timestamps en UTC, pero el monitor los mostraba sin convertir a hora local y comparaba contra la hora local del equipo. En zonas como UTC-5 la columna "Hora" salia desfasada 5 horas y el aviso de "cuota excedida" permanecia visible horas despues de recuperarse. Ahora la hora se muestra en zona local y el calculo del circuito se hace en UTC.
- El mensaje de circuito abierto en NAP Monitor ahora indica el proveedor real (Groq o Gemini) en lugar de asumir Gemini.

### Mejorado

- **Rotacion de logs**: `nap.log` rota a los 5 MB con 3 respaldos. Antes crecia sin limite en un servicio que corre 24/7.
- **Indices SQLite**: nuevos indices en `files.status`, `classification_events.timestamp`, `classification_events.file_id` y `actions_log.file_id`. Con carpetas de decenas de miles de archivos, las consultas por pendientes y el refresco del monitor escaneaban las tablas completas. Los indices se crean automaticamente al arrancar (migracion via `IF NOT EXISTS`).
- `requirements.txt` ahora declara versiones minimas verificadas.
- Integracion continua con GitHub Actions: la suite de tests corre en Windows y Ubuntu (Python 3.10 y 3.12) en cada push y pull request.

### Interno

- Limpieza de imports muertos y del hack `__import__("time")` en el orquestador.
- 4 tests nuevos cubren la restauracion del tiempo de recuperacion de los circuit breakers (54 tests en total).

## v1.0.0

- Release inicial de NAP Files-Sorter.
