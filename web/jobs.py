import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor


class Job:
    def __init__(self, payload):
        now = time.time()
        self.id = "j_" + uuid.uuid4().hex[:12]
        self.payload = payload
        self.state = "queued"
        self.stage = "idle"
        self.stage_label = "等待处理"
        self.progress = {"done": 0, "total": 0}
        self.logs = deque(maxlen=2000)
        self.events = deque(maxlen=4000)
        self.seq = 0
        self.results = []
        self.error = None
        self.stats = {
            "device": None,
            "lang_detected": None,
            "segments": 0,
            "sentences": 0,
            "removed_noise": 0,
            "translated_ok": 0,
            "elapsed_s": None,
        }
        self.created_at = now
        self.started_at = None
        self.finished_at = None
        self.stop_event = threading.Event()
        self.condition = threading.Condition()

    def emit(self, event):
        with self.condition:
            self.seq += 1
            event = dict(event, seq=self.seq)
            self.events.append(event)
            if event.get("type") == "log":
                self.logs.append({
                    "t": time.time(),
                    "msg": event["msg"],
                    "level": event.get("level", "info"),
                    "seq": self.seq,
                })
            self.condition.notify_all()

    def snapshot(self, logs_limit=500):
        with self.condition:
            elapsed = self.stats.get("elapsed_s")
            if elapsed is None and self.started_at:
                elapsed = max(0, time.time() - self.started_at)
            return {
                "id": self.id,
                "state": self.state,
                "stage": self.stage,
                "stage_label": self.stage_label,
                "files_total": len(self.payload.get("audio_paths", [])),
                "file_index": self.payload.get("file_index", 0),
                "file_name": self.payload.get("file_name", ""),
                "progress": dict(self.progress),
                "logs": list(self.logs)[-logs_limit:] if logs_limit else [],
                "results": list(self.results),
                "error": self.error,
                "stats": dict(self.stats, elapsed_s=elapsed),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


class JobsManager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio2sub")

    def create(self, payload, runner):
        job = Job(payload)
        with self.lock:
            self.jobs[job.id] = job
        self.executor.submit(self._run, job, runner)
        return job

    def _run(self, job, runner):
        job.state = "running"
        job.started_at = time.time()
        job.emit({"type": "state", "state": "running"})
        try:
            runner(job)
            if job.stop_event.is_set():
                self.finish(job, "cancelled")
            elif job.state == "running":
                self.finish(job, "done")
        except Exception as exc:
            job.error = str(exc)
            self.log(job, f"✗ 出错: {exc}", "error")
            self.finish(job, "failed")

    def finish(self, job, state):
        job.state = state
        job.finished_at = time.time()
        if job.started_at:
            job.stats["elapsed_s"] = max(0, job.finished_at - job.started_at)
        job.emit({"type": state})

    def log(self, job, msg, level="info"):
        job.emit({"type": "log", "msg": msg, "level": level})

    def stage(self, job, stage, label, done=None, total=None):
        job.stage = stage
        job.stage_label = label
        if done is not None:
            job.progress = {"done": done, "total": total or 0}
        job.emit({
            "type": "stage",
            "stage": stage,
            "label": label,
            "progress": dict(job.progress),
        })

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def summaries(self):
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)[:50]
            return [j.snapshot(logs_limit=0) for j in jobs]

    def stream(self, job, after=0):
        cursor = after
        terminal_deadline = None
        while True:
            with job.condition:
                pending = [event for event in job.events if event["seq"] > cursor]
                terminal = job.state in {"done", "failed", "cancelled"}
                if not pending and not terminal:
                    job.condition.wait(timeout=15)
                    pending = [event for event in job.events if event["seq"] > cursor]
                    terminal = job.state in {"done", "failed", "cancelled"}
                for event in pending:
                    cursor = event["seq"]
                    yield event
                if terminal and terminal_deadline is None:
                    terminal_deadline = time.time() + 5
                if terminal and not pending and time.time() >= terminal_deadline:
                    break


manager = JobsManager()
