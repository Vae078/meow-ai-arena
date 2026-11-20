@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

echo ============================================
echo   WARFARE - AI vs AI battle
echo ============================================
echo.

python live\run_live.py --map shanheguan --ai live\sideA.local.json live\sideB.local.json -o live\out

echo.
echo --------------------------------------------------
echo Result: live\out\result.json
echo Logs  : live\out\logs\g0_p0.txt  (and g0_p1.txt)
echo Replay: live\out\replays\last.wrp
echo --------------------------------------------------
echo.
pause
endlocal
