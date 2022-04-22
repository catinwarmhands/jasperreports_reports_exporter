@echo off

if not exist venv (
    echo Creating venv
    call create_venv.bat
)

echo Activating venv
call venv\Scripts\activate.bat

echo Running mypy
mypy exporter.py

if %errorlevel% neq 0 (
    echo There were errors in type checking!
    echo Cleanning temp files
    call cleanup
    pause
    exit %errorlevel%
)

echo Running pyinstaller
pyinstaller ^
--clean ^
--onefile ^
--add-data "exporter.py;." ^
--exclude-module matplotlib ^
exporter.py

if exist ..\exporter.exe (
    echo Removing ..\exporter.exe
    del /Q ..\exporter.exe
)

echo Moving executable to ..\
move dist\exporter.exe ..

echo Cleanning temp files
call cleanup

echo Deactivating venv
call venv\Scripts\deactivate.bat

pause