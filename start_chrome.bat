@echo off
set "ROOT=%~dp0"
set "PROFILE=%ROOT%data\chrome_profile"
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%PROFILE%" ^
  --no-first-run ^
  --no-default-browser-check ^
  --disable-extensions ^
  https://invest.firstrade.com/app/balance
