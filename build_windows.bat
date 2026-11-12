@echo off
REM Construit DepannagePC.exe (un seul fichier, a copier sur une cle USB)
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --onefile --console --name DepannagePC --clean depannage.py || goto :err
echo.
echo OK : dist\DepannagePC.exe
pause
exit /b 0
:err
echo ECHEC de la construction.
pause
exit /b 1
