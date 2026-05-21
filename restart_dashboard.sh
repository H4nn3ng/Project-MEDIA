#!/bin/bash
# Kill whatever is currently on port 5050, then restart the dashboard.
lsof -ti tcp:5050 | xargs kill -9 2>/dev/null
sleep 0.5
# Open browser once the server is up
(sleep 1.5 && xdg-open http://127.0.0.1:5050) &
source venv/bin/activate && python3 run_dashboard.py
