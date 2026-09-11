"""Filesystem-backed task state and artifact storage."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import tempfile
from typing import Any

from .models import StageRecord, Task, TaskManifest, utc_now


class TaskStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        if not task_id or task_id in {".", ".."} or any(c in task_id for c in '/\\:'):
            raise ValueError("task_id must be a single directory name")
        return self.root / task_id

    def manifest_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "manifest.json"

    def path(self, task_id: str) -> Path:
        """Legacy alias for the old task.json location."""
        return self.task_dir(task_id) / "task.json"

    @staticmethod
    def _atomic_json(path: Path, value: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def create(self, task_id: str, audio_path: str | Path, config: dict[str, Any] | None = None) -> TaskManifest:
        directory = self.task_dir(task_id)
        directory.mkdir(exist_ok=False)
        manifest = TaskManifest(task_id=task_id, audio_path=str(Path(audio_path).resolve()), config=config or {})
        self.save_manifest(manifest)
        for name in ("audio", "exports", "logs"):
            (self.task_dir(task_id) / name).mkdir(exist_ok=True)
        return manifest

    def save_manifest(self, manifest: TaskManifest) -> Path:
        manifest.updated_at = utc_now()
        return self._atomic_json(self.manifest_path(manifest.task_id), manifest.to_dict())

    def load_manifest(self, task_id: str) -> TaskManifest:
        return TaskManifest.from_dict(json.loads(self.manifest_path(task_id).read_text(encoding="utf-8")))

    def save_artifact(self, task_id: str, name: str, value: Any) -> Path:
        directory = self.task_dir(task_id).resolve()
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or path == directory:
            raise ValueError("artifact must stay inside the task directory")
        if path == self.manifest_path(task_id).resolve():
            raise ValueError("use save_manifest to update task state")
        return self._atomic_json(path, value.to_dict() if hasattr(value, "to_dict") else value)

    def begin_stage(self, task_id: str, stage: str) -> TaskManifest:
        manifest = self.load_manifest(task_id)
        record = manifest.stages.setdefault(stage, StageRecord())
        record.status, record.started_at, record.finished_at = "running", utc_now(), None
        record.error = None
        record.artifacts = []
        record.attempts += 1
        manifest.current_stage = stage
        self.save_manifest(manifest)
        return manifest

    def finish_stage(self, task_id: str, stage: str, artifacts: list[str] | None = None) -> TaskManifest:
        manifest = self.load_manifest(task_id)
        record = manifest.stages.setdefault(stage, StageRecord())
        record.status, record.finished_at = "completed", utc_now()
        record.error = None
        if artifacts is not None:
            record.artifacts = list(artifacts)
        self.save_manifest(manifest)
        return manifest

    def fail_stage(self, task_id: str, stage: str, error: dict[str, Any]) -> TaskManifest:
        manifest = self.load_manifest(task_id)
        record = manifest.stages.setdefault(stage, StageRecord())
        record.status, record.finished_at, record.error = "failed", utc_now(), error
        self.save_manifest(manifest)
        return manifest

    def update_stage(self, task_id: str, stage: str):
        task = self.load(task_id)
        task.stage = stage
        if self.manifest_path(task_id).exists():
            manifest = self.load_manifest(task_id)
            manifest.current_stage = stage
            self.save_manifest(manifest)
        return self.save(task)

    def load(self, task_id: str) -> Task:
        source = self.path(task_id) if self.path(task_id).exists() else self.manifest_path(task_id)
        return Task.from_dict(json.loads(source.read_text(encoding="utf-8")))

    def save(self, task: Task):
        return self._atomic_json(self.path(task.id), task.to_dict())

    def set_input_hash(self, task_id: str) -> TaskManifest:
        manifest = self.load_manifest(task_id)
        digest = hashlib.sha256()
        with Path(manifest.audio_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest.input_sha256 = digest.hexdigest()
        self.save_manifest(manifest)
        return manifest

    def manifest(self):
        return [{"id": path.parent.name, "stage": json.loads(path.read_text(encoding="utf-8")).get("current_stage")} for path in self.root.glob("*/manifest.json")]
