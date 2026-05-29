@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "DEFAULT_EXE=%SCRIPT_DIR%..\dist\NAPBackground\NAPBackground.exe"

if "%~1"=="" (
    set "NAP_EXE=%DEFAULT_EXE%"
) else (
    set "NAP_EXE=%~1"
)

for %%I in ("%NAP_EXE%") do set "NAP_EXE=%%~fI"

if not exist "%NAP_EXE%" (
    echo No se encontro el ejecutable en:
    echo   %NAP_EXE%
    echo.
    echo Genera primero los ejecutables con build_all.bat o pasa la ruta como argumento:
    echo   install_startup.bat "C:\ruta\a\NAPBackground.exe"
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$startup=[Environment]::GetFolderPath('Startup');" ^
  "$shortcut=Join-Path $startup 'NAP Files-Sorter.lnk';" ^
  "$shell=New-Object -ComObject WScript.Shell;" ^
  "$link=$shell.CreateShortcut($shortcut);" ^
  "$link.TargetPath='%NAP_EXE%';" ^
  "$link.WorkingDirectory=Split-Path '%NAP_EXE%';" ^
  "$link.Arguments='--no-wizard';" ^
  "$link.WindowStyle=7;" ^
  "$link.Save();" ^
  "Write-Host 'Acceso directo creado en:' $shortcut"

if errorlevel 1 (
    echo No se pudo crear el acceso directo de inicio.
    exit /b 1
)

echo NAP Files-Sorter (segundo plano) se ejecutara al iniciar sesion del usuario actual.
exit /b 0
