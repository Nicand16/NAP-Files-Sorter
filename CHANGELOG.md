# Changelog

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
