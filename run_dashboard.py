#!/usr/bin/env python3
"""
run_dashboard.py — start the Healing Agent dashboard.

Usage:
  python run_dashboard.py            # http://localhost:5500
  python run_dashboard.py --port 8080
"""

import argparse
import webbrowser
import threading
import time

from dashboard.app import app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5500)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"

    if not args.no_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"Healing Agent dashboard → {url}")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
