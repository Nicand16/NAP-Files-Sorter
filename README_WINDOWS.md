# NAP Files-Sorter en Windows - Desarrollo y Build

Esta guia es para desarrollo local. Para usuarios finales usa `nap_v1.0.0.zip` y `Install.bat`.

## Entorno

```powershell
cd "C:\ruta\a\Files-Sorter"
python -m venv briner_agent\.venv
briner_agent\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r briner_agent\requirements.txt
```

## Ejecutar Desde Codigo Fuente

```powershell
cd briner_agent
python main.py --setup
python main.py --once
python main.py
```

Comandos utiles:

```powershell
python main.py --metrics
python main.py --undo-last
python main.py --once --dry-run
```

## Tests

```powershell
cd briner_agent
python -m pytest tests/ -q
```

Resultado esperado para esta release:

```text
50 passed
```

## Build de Ejecutables

Desde `briner_agent`:

```powershell
python -m PyInstaller --clean --noconfirm NAPSorter.spec
python -m PyInstaller --clean --noconfirm NAPBackground.spec
python -m PyInstaller --clean --noconfirm NAPMonitor.spec
```

Artefactos esperados:

```text
briner_agent\dist\NAPSorter\NAPSorter.exe
briner_agent\dist\NAPBackground\NAPBackground.exe
briner_agent\dist\NAPMonitor\NAPMonitor.exe
```

`NAPSorter.exe` tiene consola para setup/diagnostico. `NAPBackground.exe` corre sin consola. `NAPMonitor.exe` es la UI para usuarios.

## Crear Release ZIP

Desde la raiz del repo:

```powershell
New-Item -ItemType Directory -Force release\nap_v1.0.0 | Out-Null
Copy-Item -Recurse -Force briner_agent\dist\NAPSorter,briner_agent\dist\NAPBackground,briner_agent\dist\NAPMonitor release\nap_v1.0.0\
Copy-Item -Force Install.bat,README.md,README_WINDOWS.md,MANUAL_USO.md release\nap_v1.0.0\
Compress-Archive -Path "release\nap_v1.0.0\*" -DestinationPath "nap_v1.0.0.zip" -Force
```

Validacion minima:

```powershell
Test-Path .\nap_v1.0.0.zip
Test-Path .\briner_agent\dist\NAPSorter\NAPSorter.exe
Test-Path .\briner_agent\dist\NAPBackground\NAPBackground.exe
Test-Path .\briner_agent\dist\NAPMonitor\NAPMonitor.exe
tar -tf .\nap_v1.0.0.zip | Select-String "README.md|README_WINDOWS.md|MANUAL_USO.md|Install.bat"
```

## Notas de Runtime

- Configuracion del usuario: `%APPDATA%\NAP Files-Sorter\user_settings.json`.
- API key: `%APPDATA%\NAP Files-Sorter\.env`.
- Base de datos: `%APPDATA%\NAP Files-Sorter\nap.db`.
- Logs: `%APPDATA%\NAP Files-Sorter\logs\nap.log`.
- IPC Monitor/Tray/Background: `%APPDATA%\NAP Files-Sorter\commands\*.json`.
- Escaneo inmediato: `%APPDATA%\NAP Files-Sorter\.force_scan`.

## Modo Realtime Opcional

El modo recomendado para usuarios finales es `interval`. Si necesitas realtime para pruebas, cambia `monitoring.mode` a `realtime` en config o settings.
