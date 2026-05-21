#!/bin/bash
lsof -ti tcp:5500 | xargs kill -9 2>/dev/null
sleep 0.5
source agent1/bin/activate && python3 run_dashboard.py
