@echo off
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%USERPROFILE%\AppData\Local\Google\Chrome\CDPProfile" ^
  https://invest.firstrade.com
