@echo off

if exist .mypy_cache\ (
    echo Removing .mypy_cache
    rmdir /S /Q .mypy_cache\
)

if exist output\ (
    echo Removing output
    rmdir /S /Q output\
)

if exist log\ (
    echo Removing log
    rmdir /S /Q log\
)

if exist ..\output\ (
    echo Removing ..\output
    rmdir /S /Q ..\output\
)

if exist ..\log\ (
    echo Removing ..\log
    rmdir /S /Q ..\log\
)

if exist __pycache__\ (
    echo Removing __pycache__\
    rmdir /S /Q __pycache__\
)

if exist build\ (
    echo Removing build\
    rmdir /S /Q build\
)
if exist dist\ (
    echo Removing dist\
    rmdir /S /Q dist\
)

if exist *.spec (
    echo Removing *.spec
    del /q *.spec
)
