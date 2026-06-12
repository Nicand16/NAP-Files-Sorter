@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

echo.
echo  =====================================================
echo    NAP Files-Sorter - Instalador
echo  =====================================================
echo.

set "ROOT=%~dp0"
set "NAP_EXE=%ROOT%NAPSorter\NAPSorter.exe"
set "NAP_BG_EXE=%ROOT%NAPBackground\NAPBackground.exe"
set "NAP_MON_EXE=%ROOT%NAPMonitor\NAPMonitor.exe"

:: --- Verificar archivos necesarios ---
if not exist "%NAP_EXE%" (
    echo  ERROR: No se encontro NAPSorter.exe en:
    echo    %NAP_EXE%
    echo.
    echo  Asegurate de que Install.bat este en la misma carpeta que las carpetas NAPSorter,
    echo  NAPBackground y NAPMonitor.
    echo  Si descargaste el zip de GitHub, extrae todo antes de ejecutar Install.bat.
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%NAPSorter\_internal\python314.dll" (
    echo  ERROR: Archivos internos faltantes ^(_internal\python314.dll^).
    echo  La descarga parece incompleta. Descarga el repositorio de nuevo.
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%NAPSorter\_internal\_socket.pyd" (
    echo  ERROR: Archivo _socket.pyd faltante.
    echo  La descarga parece incompleta. Descarga el repositorio de nuevo.
    echo.
    pause
    exit /b 1
)

:: --- Seleccion de carpeta ---
echo  Selecciona la carpeta que deseas que NAP Files-Sorter organice.
echo  ^(Abriendo dialogo de seleccion -- puede tardar unos segundos^)
echo.

set "WATCH_DIR="
set "PS_TMP=%TEMP%\nap_picker_%RANDOM%.ps1"

:: Escribir script PS a fichero temporal para evitar problemas de escape
(
    echo Add-Type -AssemblyName System.Windows.Forms
    echo [System.Windows.Forms.Application]::EnableVisualStyles^(^)
    echo $anchor = New-Object System.Windows.Forms.Form
    echo $anchor.TopMost = $true
    echo $anchor.WindowState = 'Minimized'
    echo $anchor.ShowInTaskbar = $false
    echo $anchor.Show^(^)
    echo $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    echo $dlg.Description = 'Selecciona la carpeta que NAP Files-Sorter organizara automaticamente'
    echo $dlg.ShowNewFolderButton = $true
    echo if ^($dlg.ShowDialog^($anchor^) -eq 'OK'^) ^{ Write-Output $dlg.SelectedPath ^}
    echo $anchor.Dispose^(^)
) > "%PS_TMP%"

for /f "usebackq delims=" %%F in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_TMP%"`) do (
    set "WATCH_DIR=%%F"
)
del "%PS_TMP%" >nul 2>&1

:: Si el dialogo fallo o fue cancelado, pedir ruta por texto
if "!WATCH_DIR!"=="" (
    echo  No se selecciono carpeta en el dialogo.
    echo  Escribe la ruta de la carpeta directamente:
    echo.
    set /p "WATCH_DIR=  Carpeta ^(ej: C:\Users\tu_usuario\Downloads^): "
    set "WATCH_DIR=!WATCH_DIR:"=!"
)

if "!WATCH_DIR!"=="" (
    echo.
    echo  No se indico ninguna carpeta. Instalacion cancelada.
    echo.
    pause
    exit /b 1
)

echo.
echo  Carpeta seleccionada: !WATCH_DIR!
echo.

:: --- API key de Groq ---
echo  Necesitas una API key de Groq ^(gratuita — 14.400 solicitudes al dia^).
echo  Obtenla en: https://console.groq.com
echo.
set /p "GROQ_KEY=  Pega tu API key aqui: "
set "GROQ_KEY=!GROQ_KEY: =!"
set GROQ_KEY=!GROQ_KEY:"=!

if "!GROQ_KEY!"=="" (
    echo.
    echo  No se ingreso ninguna API key. Instalacion cancelada.
    echo.
    pause
    exit /b 1
)

if /i not "!GROQ_KEY:~0,4!"=="gsk_" (
    echo.
    echo  AVISO: las API keys de Groq normalmente empiezan con "gsk_".
    echo  Verifica en https://console.groq.com que copiaste la key completa.
    echo  Puedes cambiarla despues desde NAP Monitor ^(boton de Configuracion^).
)
echo.

:: --- Guardar API key en APPDATA\NAP Files-Sorter\.env ---
if not exist "%APPDATA%\NAP Files-Sorter" mkdir "%APPDATA%\NAP Files-Sorter"
echo GROQ_API_KEY=!GROQ_KEY!> "%APPDATA%\NAP Files-Sorter\.env"

:: --- Configurar NAP Files-Sorter ---
echo  Configurando NAP Files-Sorter...
"%NAP_EXE%" --setup --watch-dir "!WATCH_DIR!"
set "SETUP_ERR=!ERRORLEVEL!"

if !SETUP_ERR! neq 0 (
    echo.
    echo  ERROR ^(!SETUP_ERR!^) al configurar NAP Files-Sorter.
    echo  Revisa los logs en: %APPDATA%\NAP Files-Sorter\logs\nap.log
    echo.
    pause
    exit /b 1
)

:: --- Iniciar en segundo plano ---
if exist "%NAP_BG_EXE%" (
    echo.
    echo  Iniciando NAP Files-Sorter en segundo plano...
    taskkill /F /IM NAPBackground.exe /T >nul 2>&1
    timeout /t 1 /nobreak >nul
    start "" "%NAP_BG_EXE%" --no-wizard
    echo  NAP Files-Sorter esta corriendo. Revisara tu carpeta cada hora.
) else (
    echo.
    echo  Nota: NAPBackground.exe no encontrado junto a NAPSorter.exe.
    echo  NAP Files-Sorter se iniciara automaticamente en el proximo inicio de Windows.
)

:: --- Acceso directo al monitor en el Escritorio ---
if exist "%NAP_MON_EXE%" (
    set "LNK_TMP=%TEMP%\nap_monitor_lnk_%RANDOM%.ps1"
    (
        echo $desktop = [Environment]::GetFolderPath^('Desktop'^)
        echo $lnk = Join-Path $desktop 'NAP Monitor.lnk'
        echo $shell = New-Object -ComObject WScript.Shell
        echo $link = $shell.CreateShortcut^($lnk^)
        echo $link.TargetPath = '!NAP_MON_EXE!'
        echo $link.Description = 'Ver actividad de NAP Files-Sorter en tiempo real'
        echo $link.Save^(^)
    ) > "!LNK_TMP!"
    powershell -NoProfile -ExecutionPolicy Bypass -File "!LNK_TMP!" >nul 2>&1
    del "!LNK_TMP!" >nul 2>&1
    echo.
    echo  Acceso directo "NAP Monitor" creado en el Escritorio.
)

:: --- Abrir monitor inmediatamente para verificar que funciona ---
if exist "%NAP_MON_EXE%" (
    echo.
    echo  Abriendo NAP Monitor...
    start "" "%NAP_MON_EXE%"
)

echo.
echo  =====================================================
echo    Instalacion completada
echo  =====================================================
echo.
echo  Carpeta monitoreada : !WATCH_DIR!
echo  Frecuencia          : cada hora
echo  Inicio automatico   : al arrancar Windows
echo  Logs                : %APPDATA%\NAP Files-Sorter\logs\nap.log
echo  Monitor             : "NAP Monitor" en el Escritorio ^(se abrio ahora^)
echo.
echo  Icono de bandeja    : busca el circulo de colores en la bandeja
echo                        del sistema ^(esquina inferior derecha^).
echo                        Si no lo ves, haz clic en la flecha ^ para
echo                        ver los iconos ocultos.
echo.
pause
