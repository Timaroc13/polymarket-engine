@echo off
rem Starts the polymarket-engine server (scheduler runs inside it).
rem Launched hidden at logon by the PolymarketEngine_Server scheduled task.
cd /d "C:\dev\crypto-news-parser"
if not exist logs mkdir logs
for /f "usebackq" %%d in (`powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"`) do set TODAY=%%d
"C:\dev\crypto-news-parser\.venv\Scripts\python.exe" -m uvicorn crypto_news_parser.main:app --app-dir src --port 8000 --env-file .env >> "logs\server_%TODAY%.log" 2>&1
