@echo off
title iRacing Publisher v1
cd /d %~dp0

call .venv\Scripts\activate

py tracker_publisher.py --config config.example.json

pause
