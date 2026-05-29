@echo off
setlocal
cd /d "%~dp0"

echo.
echo  =====================================================
echo    NAP Files-Sorter - Compilador de ejecutables
echo  =====================================================
echo.

:: Activar venv si existe (buscar en distintas ubicaciones)
set "VENV_ACTIVATE="
if exist ".venv\Scripts\activate.bat"     set "VENV_ACTIVATE=.venv\Scripts\activate.bat"
if exist "..\.venv\Scripts\activate.bat"  set "VENV_ACTIVATE=..\.venv\Scripts\activate.bat"
if exist "..\venv\Scripts\activate.bat"   set "VENV_ACTIVATE=..\venv\Scripts\activate.bat"

if not "%VENV_ACTIVATE%"=="" (
    call "%VENV_ACTIVATE%"
    echo  Entorno virtual activado.
) else (
    echo  Usando Python del sistema.
)

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: PyInstaller no encontrado.
    echo  Instala con:  pip install pyinstaller
    pause
    exit /b 1
)

echo.
echo  Compilando NAPSorter.exe (con consola para configuracion)...
python -m PyInstaller --clean --noconfirm NAPSorter.spec
if errorlevel 1 (
    echo  ERROR al compilar NAPSorter.exe
    pause
    exit /b 1
)

echo.
echo  Compilando NAPBackground.exe (sin consola, para segundo plano)...
python -m PyInstaller --clean --noconfirm NAPBackground.spec
if errorlevel 1 (
    echo  ERROR al compilar NAPBackground.exe
    pause
    exit /b 1
)

echo.
echo  Compilando NAPMonitor.exe (ventana de monitoreo en tiempo real)...
python -m PyInstaller --clean --noconfirm NAPMonitor.spec
if errorlevel 1 (
    echo  ERROR al compilar NAPMonitor.exe
    pause
    exit /b 1
)

echo.
echo  =====================================================
echo    Compilacion exitosa
echo  =====================================================
echo.
echo  dist\NAPSorter\NAPSorter.exe                  (setup y diagnostico)
echo  dist\NAPBackground\NAPBackground.exe          (servicio en fondo)
echo  dist\NAPMonitor\NAPMonitor.exe                (monitor en tiempo real)
echo.
pause
