@echo off
setlocal enabledelayedexpansion

:: 1. Recupera la cartella in cui risiede lo script
set "REPO_ROOT=%~dp0"
pushd "%REPO_ROOT%"

:: 2. Recupera l'URL base del repository Git
for /f "tokens=*" %%A in ('git config --get remote.origin.url 2^>nul') do (
    set "BASE_URL=%%A"
)

if "%BASE_URL%"=="" (
    echo Errore: Questa cartella non e un repository Git valido o non ha un remote origin.
    popd
    pause
    exit /b
)

:: Pulisce l'URL del repo (rimuove .git finale e uniforma SSH in HTTPS)
if "%BASE_URL:~0,4%"=="git@" (
    set "BASE_URL=%BASE_URL:git@github.com:=https://github.com%"
)
if "%BASE_URL:~-4%"==".git" (
    set "BASE_URL=%BASE_URL:~0,-4%"
)

:: 3. Recupera il branch corrente attivo
for /f "tokens=*" %%B in ('git branch --show-current 2^>nul') do (
    set "BRANCH=%%B"
)
if "%BRANCH%"=="" set "BRANCH=main"

:: Prefisso comune per velocizzare la stringa finale
set "PREFIX=%BASE_URL%/blob/%BRANCH%"

:: 4. Elenca i file usando Git ed esclude i pattern con findstr (ricerca parziale migliorata)
::    Usa espressioni regolari per bloccare qualsiasi stringa che contenga test, proto, third_party o punti
for /f "tokens=*" %%F in ('git ls-files 2^>nul ^| findstr /v /i /r /c:"test" /c:"proto" /c:"third_party" /c:"__init__" /c:"^\." /c:"/\."') do (
    echo %PREFIX%/%%F
)

popd
pause
