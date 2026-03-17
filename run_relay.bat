@echo off
title iRacing Relay Server v1
cd /d %~dp0

call .venv\Scripts\activate

set WRITE_TOKEN=change-me-write-token
set READ_TOKEN=change-me-read-token

py relay_server.py --host 0.0.0.0 --port 8000

pause
