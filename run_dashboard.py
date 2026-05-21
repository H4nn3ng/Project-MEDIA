#!/usr/bin/env python3
"""
run_dashboard.py — Start the Money Psychology pipeline dashboard.

Usage:
    python3 run_dashboard.py             # default: localhost:5050
    python3 run_dashboard.py --port 8080
    python3 run_dashboard.py --host 0.0.0.0  # expose on LAN
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dashboard.app import app


def main():
    parser = argparse.ArgumentParser(description="Money Psychology dashboard")
    parser.add_argument("--host",  default="127.0.0.1")
    parser.add_argument("--port",  type=int, default=5050)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"\n  Money Psychology Dashboard")
    print(f"  http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
