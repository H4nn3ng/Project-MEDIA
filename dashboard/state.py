import collections
import queue
import threading
import time


class PipelineState:
    def __init__(self):
        self._lock          = threading.Lock()
        self.running: str | None = None       # command key e.g. "agent", "carousel"
        self.started_at: float | None = None
        self.current_step: str = ""
        self.step_status: dict[str, str] = {} # step_key -> "done"|"running"|"error"
        self.last_exit_code: int | None = None
        self.last_command: str | None = None
        self.log_buffer: collections.deque = collections.deque(maxlen=2000)
        self.sse_queues: list[queue.Queue] = []
        self._process = None

    # ------------------------------------------------------------------ lock

    def acquire(self):
        return self._lock

    # -------------------------------------------------------------- run start

    def start(self, command_key: str, process):
        with self._lock:
            self.running      = command_key
            self.started_at   = time.monotonic()
            self.current_step = ""
            self.step_status  = {}
            self._process     = process
        self._broadcast_status()

    # --------------------------------------------------------------- run end

    def finish(self, exit_code: int):
        with self._lock:
            # Mark last active step as error if non-zero
            if exit_code != 0 and self.current_step:
                self.step_status[self.current_step] = "error"
            elif self.current_step:
                self.step_status[self.current_step] = "done"
            self.last_exit_code = exit_code
            self.last_command   = self.running
            self.running        = None
            self.started_at     = None
            self._process       = None
        self._broadcast_status()

    # ---------------------------------------------------------- step parsing

    def advance_step(self, new_step: str):
        with self._lock:
            if self.current_step and self.current_step != new_step:
                self.step_status[self.current_step] = "done"
            self.current_step = new_step
            self.step_status[new_step] = "running"
        self._broadcast_status()

    # ---------------------------------------------------------- log line

    def push_log(self, line: str):
        with self._lock:
            self.log_buffer.append(line)
            queues = list(self.sse_queues)
        for q in queues:
            try:
                q.put_nowait(("log", line))
            except queue.Full:
                pass

    # ---------------------------------------------------------- SSE clients

    def add_sse_client(self) -> queue.Queue:
        q = queue.Queue(maxsize=500)
        with self._lock:
            self.sse_queues.append(q)
        return q

    def remove_sse_client(self, q: queue.Queue):
        with self._lock:
            try:
                self.sse_queues.remove(q)
            except ValueError:
                pass

    # ---------------------------------------------------------- broadcast

    def _broadcast_status(self):
        import json
        payload = json.dumps(self.snapshot())
        with self._lock:
            queues = list(self.sse_queues)
        for q in queues:
            try:
                q.put_nowait(("status", payload))
            except queue.Full:
                pass

    # ---------------------------------------------------------- snapshot

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = (
                round(time.monotonic() - self.started_at)
                if self.started_at else None
            )
            return {
                "running":       self.running,
                "current_step":  self.current_step,
                "step_status":   dict(self.step_status),
                "last_exit_code": self.last_exit_code,
                "last_command":  self.last_command,
                "elapsed_s":     elapsed,
            }

    # ---------------------------------------------------------- stop

    def stop(self):
        with self._lock:
            proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                import time as _t
                _t.sleep(5)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass


state = PipelineState()
