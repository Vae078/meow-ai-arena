@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   AI Live Battle - launching 3 windows
echo   [1] battle (main)
echo   [2] watch side A (seat 0)
echo   [3] watch side B (seat 1)
echo ============================================

start "battle-main" cmd /k "chcp 65001 >nul & python battle\live\run_live.py --deploy battle\bots\gpt-6.2-sol\duel.json battle\bots\gpt-6.2-sol\duel.json --ai battle\live\sideA.local.json battle\live\sideB.local.json -o battle\live\out"

start "watch-sideA" cmd /k "chcp 65001 >nul & tail -F battle\live\out\logs\g0_p0.txt"

start "watch-sideB" cmd /k "chcp 65001 >nul & tail -F battle\live\out\logs\g0_p1.txt"

endlocal
