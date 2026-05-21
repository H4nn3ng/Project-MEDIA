"""
runner.py — launch pipeline subprocesses and stream their output.

All subprocesses share one state singleton. Only one can run at a time.
stdout is streamed line-by-line to:
  - state.log_buffer (ring buffer for late-joining SSE clients)
  - all registered SSE queue (fan-out)
  - step parser (updates state.step_status / state.current_step)
"""

import re
import subprocess
import sys
import threading
from pathlib import Path

from .state import state
from .paths import BASE_DIR

# Maps log prefixes to step keys shown in the UI
_STEP_RE = re.compile(r'\[(step\d+[b]?)\]', re.IGNORECASE)
_EDITOR_STEPS = {
    "[carousel]": "carousel",
    "[story]":    "story",
    "[reel]":     "reel",
}

AGENT_STEPS_ORDERED = [
    "step1", "step3", "step3b",
    "step4", "step4b", "step5",
    "step6", "step6b", "step7", "step8",
]
AGENT_QUICK_STEPS = ["step1", "step3", "step3b"]


def _parse_step(line: str) -> str | None:
    m = _STEP_RE.search(line)
    if m:
        return m.group(1).lower()
    for prefix, key in _EDITOR_STEPS.items():
        if line.startswith(prefix):
            return key
    return None


def _reader_thread(proc, command_key: str):
    """Read subprocess stdout line-by-line, update state, fan-out to SSE."""
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            state.push_log(line)
            step = _parse_step(line)
            if step:
                state.advance_step(step)
    except Exception:
        pass
    finally:
        proc.stdout.close()
        code = proc.wait()
        state.finish(code)


def launch(command_key: str, cmd: list[str]) -> tuple[bool, str]:
    """
    Start a subprocess if none is running.
    Returns (started: bool, error_message: str).
    """
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

    t = threading.Thread(target=_reader_thread, args=(proc, command_key), daemon=True)
    t.start()
    return True, ""


def stop() -> bool:
    state.stop()
    return True


def _env_with_venv() -> dict:
    """Return current env with the venv's bin prepended to PATH."""
    import os
    env = os.environ.copy()
    venv_bin = BASE_DIR / "agent1" / "bin"
    if venv_bin.exists():
        env["PATH"] = str(venv_bin) + ":" + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(BASE_DIR / "agent1")
    return env
