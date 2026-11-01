@echo off
setlocal EnableExtensions
pushd "%~dp0"
title Pocket 3 PTZ EXE Builder

echo ============================================================
echo Pocket 3 PTZ Windows EXE Builder
echo ============================================================
echo Working folder: %CD%
echo.

py -3.12 --version >nul 2>nul
if not errorlevel 1 goto :use_py312
py -3 --version >nul 2>nul
if not errorlevel 1 goto :use_py3
python --version >nul 2>nul
if not errorlevel 1 goto :use_python
goto :no_python

:use_py312
set "PYEXE=py"
set "PYARGS=-3.12"
goto :python_ready

:use_py3
set "PYEXE=py"
set "PYARGS=-3"
goto :python_ready

:use_python
set "PYEXE=python"
set "PYARGS="

:python_ready

echo [1/6] Python detected:
%PYEXE% %PYARGS% --version
echo.

echo [2/6] Installing build tools, NDI preview, and PTZ dependencies...
%PYEXE% %PYARGS% -m pip install --upgrade pyinstaller pygrabber comtypes ndi-python opencv-contrib-python-headless numpy
if errorlevel 1 goto :failed

echo.
echo [3/6] Building Pocket3-PTZ-Node.exe...
%PYEXE% %PYARGS% -m PyInstaller --noconfirm --clean --onefile --windowed --name Pocket3-PTZ-Node --add-data "director-panel.html;." --hidden-import pygrabber.dshow_graph --collect-submodules comtypes --collect-submodules cv2 ptz-server.py
if errorlevel 1 goto :failed

echo.
echo [4/6] Building Pocket3-Director.exe...
%PYEXE% %PYARGS% -m PyInstaller --noconfirm --clean --onefile --windowed --name Pocket3-Director --add-data "director-panel.html;." --hidden-import NDIlib --collect-submodules cv2 director-console.py
if errorlevel 1 goto :failed

echo.
echo [5/6] Building Pocket3-Multi-Launcher.exe...
%PYEXE% %PYARGS% -m PyInstaller --noconfirm --clean --onefile --console --name Pocket3-Multi-Launcher --hidden-import pygrabber.dshow_graph --collect-submodules comtypes multi-ptz-launcher.py
if errorlevel 1 goto :failed

echo.
echo [6/6] Copying completed EXE files...
if not exist "EXE" mkdir "EXE"
copy /y "dist\Pocket3-PTZ-Node.exe" "EXE\Pocket3-PTZ-Node.exe" >nul
if errorlevel 1 goto :failed
copy /y "dist\Pocket3-Director.exe" "EXE\Pocket3-Director.exe" >nul
if errorlevel 1 goto :failed
copy /y "dist\Pocket3-Multi-Launcher.exe" "EXE\Pocket3-Multi-Launcher.exe" >nul
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo BUILD SUCCESS
echo Output folder: %CD%\EXE
echo ============================================================
echo Pocket3-PTZ-Node.exe : install on every Pocket 3 computer
echo Pocket3-Multi-Launcher.exe : auto-detect and start up to 5 DJI cameras
echo Pocket3-Director.exe : install on the director computer
echo.
start "" explorer.exe "%CD%\EXE"
pause
popd
exit /b 0

:no_python
echo.
echo ============================================================
echo ERROR: Python 3 was not found.
echo Install Python 3.12 and enable "Add Python to PATH".
echo Then run this file again.
echo ============================================================
pause
popd
exit /b 1

:failed
echo.
echo ============================================================
echo BUILD FAILED
echo Scroll up and send a screenshot of the first red error.
echo ============================================================
pause
popd
exit /b 1
