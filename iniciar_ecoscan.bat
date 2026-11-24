@echo off
setlocal EnableExtensions
title EcoScan v3.0 - Rose
color 0A
cls

REM -- Salida en UTF-8 para que los acentos y emojis de la consola no fallen ----
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo        EcoScan v3.0 - Rose

REM -- Ir a la carpeta donde esta este .bat ------------------------------------
cd /d "%~dp0"

REM -- Buscar Python (primero el lanzador "py", luego "python") ----------------
set "PY="
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto :nopython

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :oldpython
echo  [OK] Python detectado
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo  [AVISO] Tu Python es anterior a 3.12. Funciona, pero Python 3.10 ya no recibe
    echo          actualizaciones de seguridad: instala 3.12 o 3.13 cuando puedas.
)
echo.

REM -- Entorno virtual propio (.venv): no ensucia tu Python global --------------
if not exist ".venv\Scripts\python.exe" (
    echo  Creando entorno virtual .venv ^(solo la primera vez^)...
    %PY% -m venv .venv >nul 2>&1
    if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check >nul 2>&1
)
set "VPY=%PY%"
if exist ".venv\Scripts\python.exe" set "VPY=.venv\Scripts\python.exe"

REM -- Instalar / verificar dependencias ---------------------------------------
REM Solo se reinstala si requirements.txt cambio desde la ultima vez (arranque mas rapido)
fc /b "requirements.txt" ".venv\requirements.instalado" >nul 2>&1
if not errorlevel 1 (
    echo  [OK] Dependencias al dia
    echo.
    goto :depsok
)
echo  Instalando/verificando dependencias...
%VPY% -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :piperror
if exist ".venv\Scripts" copy /y "requirements.txt" ".venv\requirements.instalado" >nul
echo  [OK] Dependencias listas
echo.
:depsok

REM -- Llave de Groq: ya NO viene dentro del codigo ----------------------------
if defined GROQ_API_KEY goto :keyok
if exist ".env" goto :keyok
echo  ------------------------------------------------------------
echo   Falta tu llave de Groq para el analisis con IA.
echo   Creala gratis en: https://console.groq.com/keys
echo   Se guarda solo en el archivo .env de esta carpeta.
echo  ------------------------------------------------------------
set "GROQ_KEY="
set /p "GROQ_KEY=  Pega tu GROQ_API_KEY (gsk_...) y pulsa Enter: "
if defined GROQ_KEY (
    > ".env" echo GROQ_API_KEY=%GROQ_KEY%
    echo  [OK] Llave guardada en .env
) else (
    echo  [AVISO] Sin llave: la app abre, pero el analisis con IA no funcionara.
)
echo.
:keyok

REM -- Evita subir secretos a GitHub por accidente -----------------------------
if not exist ".gitignore" (
    > ".gitignore" echo .env
    >> ".gitignore" echo .env.*
    >> ".gitignore" echo .secret_key
    >> ".gitignore" echo .venv/
    >> ".gitignore" echo __pycache__/
    >> ".gitignore" echo ecoscan.db
    >> ".gitignore" echo *service-account*.json
    >> ".gitignore" echo *-credentials.json
)

REM -- Iniciar servidor ---------------------------------------------------------
REM Por seguridad solo escucha en tu equipo. Para abrirlo desde el celular en tu
REM red WiFi quita "REM" de la siguiente linea ^(la camara solo funciona con HTTPS^):
REM set "HOST=0.0.0.0"
echo  Iniciando EcoScan en http://localhost:5050
echo  (El navegador abrira automaticamente)
echo.
echo  Para detener: cierra esta ventana o presiona Ctrl+C
echo.
echo  ============================================
echo.
%VPY% server.py
set "RC=%errorlevel%"
REM Codigo 0 = cierre normal; -1073741510 = Ctrl+C. Cualquier otro = el servidor cayo por un error.
if not "%RC%"=="0" if not "%RC%"=="-1073741510" (
    echo.
    echo  [AVISO] El servidor se detuvo de forma inesperada ^(codigo %RC%^).
    echo          Copia las lineas de arriba: indican la causa. Luego vuelve a abrir este archivo.
)
goto :fin

:nopython
echo  [ERROR] Python no encontrado.
echo  Descargalo en: https://www.python.org/downloads/
echo  ^(marca la casilla "Add Python to PATH" al instalar^)
goto :fin

:oldpython
echo  [ERROR] Se necesita Python 3.10 o superior ^(recomendado 3.12 o 3.13^).
echo  Descarga una version nueva en: https://www.python.org/downloads/
goto :fin

:piperror
echo  [ERROR] Fallo al instalar dependencias. Revisa tu conexion a internet.

:fin
echo.
pause