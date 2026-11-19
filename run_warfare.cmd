@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

echo ============================================
echo   WARFARE - AI vs AI battle
echo ============================================
echo.

python warfare\live\run_live.py --map huizhan --ai battle\live\sideA.local.json battle\live\sideB.local.json -o warfare\live\out

echo.
echo --------------------------------------------------
echo Result: warfare\live\out\result.json
echo Logs  : warfare\live\out\logs\g0_p0.txt  (and g0_p1.txt)
echo Replay: warfare\live\out\replays\last.wrp
echo --------------------------------------------------
echo.
pause
endlocal
