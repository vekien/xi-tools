@echo off
echo Stopping xi...
taskkill /F /IM xi.exe >nul 2>&1
timeout /t 2 /nobreak >nul
echo Reinstalling (editable)...
REM --reinstall rebuilds the tool env; --force overwrites the existing xi.exe
REM shim. Without --force the shim is left pointing at the env that
REM --reinstall just deleted, and xi fails with
REM "uv trampoline failed to canonicalize script path".
uv tool install --reinstall --force --editable D:\xi-tools
echo Done. Run "xi --help" to verify.
pause
