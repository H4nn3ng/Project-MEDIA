"""
runner.py — launch pipeline subprocesses and stream their output.

Supports two commands: scrape (scrape.py) and render (render.py).
stdout is streamed line-by-line to the SSE log and step tracker.
"""

import re
import subprocess
import threading
import time

from .state import state
from .paths import BASE_DIR

_SCRAPE_STEPS = {
    "Step 1: Scraper":              "scrape",
    "Step 2: Scoring concepts":     "score_concepts",
    "Step 3: Ingestion fact-check": "factcheck",
    "Step 4: Scoring visual/audio": "score_media",
}

_RENDER_STEPS_RE = re.compile(r"Producing:\s+\S+\s*/\s*(\w+)", re.IGNORECASE)
_API_CALL_RE     = re.compile(r"API_CALL\s+\w+")

# Kill subprocess if it runs longer than this (safety net)
_TIMEOUT_SECONDS = {
    "scrape": 12 * 60,   # 12 minutes — searches now parallel (~30s), Gemini ~4-6min
    "render": 10 * 60,   # 10 minutes
}
_DEFAULT_TIMEOUT = 12 * 60


def _parse_step(line: str) -> str | None:
    for prefix, key in _SCRAPE_STEPS.items():
        if prefix in line:
            return key
    m = _RENDER_STEPS_RE.search(line)
    if m:
        return f"render_{m.group(1).lower()}"
    return None


def _watchdog_thread(proc, command_key: str, timeout_s: int):
    """Kill the subprocess if it exceeds timeout_s seconds."""
    deadline = time.monotonic() + timeout_s
    while True:
        time.sleep(15)
        if proc.poll() is not None:
            return  # already finished
        if time.monotonic() >= deadline:
            mins = timeout_s // 60
            state.push_log(
                f"[TIMEOUT] {command_key} exceeded {mins}min limit — killing process"
            )
            try:
                proc.terminate()
                time.sleep(5)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            return


def _reader_thread(proc, command_key: str):
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            state.push_log(line)
            step = _parse_step(line)
            if step:
                state.advance_step(step)
            if _API_CALL_RE.search(line):
                state.increment_api_calls()
    except Exception:
        pass
    finally:
        proc.stdout.close()
        code = proc.wait()
        state.finish(code)


def launch(command_key: str, cmd: list[str]) -> tuple[bool, str]:
    with state.acquire():
        if state.running:
            return False, f"already_running: {state.running}"

    env = _env_with_venv()
    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    state.start(command_key, proc)

    timeout_s = _TIMEOUT_SECONDS.get(command_key, _DEFAULT_TIMEOUT)

    t = threading.Thread(target=_reader_thread, args=(proc, command_key), daemon=True)
    t.start()
    w = threading.Thread(target=_watchdog_thread, args=(proc, command_key, timeout_s), daemon=True)
    w.start()
    return True, ""


def stop() -> bool:
    state.stop()
    return True


def _venv_python() -> str:
    """Return the venv Python, falling back to sys.executable."""
    for candidate in (BASE_DIR / "venv" / "bin" / "python3",
                      BASE_DIR / "venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _env_with_venv() -> dict:
    import os, re
    env = os.environ.copy()
    venv_bin = BASE_DIR / "venv" / "bin"

    # Parse `export KEY=VALUE` lines from the activate script so the subprocess
    # gets API keys even when the dashboard was started without `source venv/bin/activate`.
    activate = venv_bin / "activate"
    if activate.exists():
        for line in activate.read_text().splitlines():
            m = re.match(r"^export\s+(\w+)=(.+)$", line.strip())
            if m:
                key = m.group(1)
                val = m.group(2).strip("\"'")
                env.setdefault(key, val)   # don't overwrite if already set in shell

    if venv_bin.exists():
        env["PATH"] = str(venv_bin) + ":" + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(BASE_DIR / "venv")
    return env
