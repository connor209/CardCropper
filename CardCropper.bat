@echo off
rem CardCropper launcher — for machines where Smart App Control blocks the .exe.
rem
rem Windows 11's Smart App Control refuses to run unsigned executables and has
rem no "run anyway" — Microsoft's own documentation says there is no way to
rem bypass it for an individual app. It governs EXECUTABLES, though, not
rem scripts, so this file starts the app through Python's own pythonw.exe,
rem which is signed by the Python Software Foundation and trusted already.
rem
rem Double-click it. The first run installs Pillow and numpy; later runs just
rem open the window.

cd /d "%~dp0"

rem The console interpreter, for the checks and the one-off install.
rem Each fallback is a block, not a one-liner. `if not defined X cmd && set`
rem looks equivalent and is not: the && binds to the IF statement's exit code,
rem so when X is already defined the set still runs and overwrites it.
set "CONSOLE="
where py >nul 2>&1 && set "CONSOLE=py -3"
if not defined CONSOLE (
    where python >nul 2>&1 && set "CONSOLE=python"
)

if not defined CONSOLE (
    echo.
    echo   Python is not installed, or is not on PATH.
    echo.
    echo   Install it from  https://www.python.org/downloads/windows/
    echo   and tick "Add python.exe to PATH" in the installer.
    echo.
    echo   Then run this file again.
    echo.
    pause
    exit /b 1
)

rem The windowed interpreter, so no console sits behind the app.
set "LAUNCH="
where pyw >nul 2>&1 && set "LAUNCH=pyw -3"
if not defined LAUNCH (
    where pythonw >nul 2>&1 && set "LAUNCH=pythonw"
)
if not defined LAUNCH set "LAUNCH=%CONSOLE%"

rem Tk is checked separately and early. It is an option in the Python
rem installer that people untick, and without it the app fails on import with
rem no window to show the error in — which looks exactly like nothing
rem happening, the very symptom this launcher exists to avoid.
%CONSOLE% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   This Python was installed without Tk, so the window cannot open.
    echo.
    echo   Re-run the Python installer, choose Modify, and enable
    echo   "tcl/tk and IDLE".
    echo.
    pause
    exit /b 1
)

%CONSOLE% -c "import PIL, numpy" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   First run — installing Pillow and numpy. This takes a minute.
    echo.
    %CONSOLE% -m pip install --user --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   Installing the dependencies failed. The message above says why.
        echo.
        pause
        exit /b 1
    )
)

start "CardCropper" %LAUNCH% -m cardcropper
