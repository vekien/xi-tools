@echo off
setlocal enabledelayedexpansion

:: -- xi-tools packager --------------------------------------------------
:: Bundles src/, web/, and docs/ into a versioned zip.
:: Skips symlink/junction folders (game, exports, custom, etc.)

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

:: Pull version from pyproject.toml
for /f "tokens=2 delims==" %%V in ('findstr /r "^version" "%ROOT%\pyproject.toml"') do (
    set "RAW=%%V"
)
set "VERSION=%RAW: =%"
set "VERSION=%VERSION:"=%"

set "PKG_ROOT=%ROOT%"
set "PKG_OUT=%ROOT%\xi-tools-%VERSION%.zip"

echo.
echo  Packaging xi-tools v%VERSION%
echo  Output : %PKG_OUT%
echo.

if exist "%PKG_OUT%" (
    echo  Removing existing %PKG_OUT%
    del /f /q "%PKG_OUT%"
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0xitools-pkg.ps1"

echo.
endlocal
