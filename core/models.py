"""Stable domain models for the Agent-native subtitle workflow."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
import json

StageStatus = Literal["pending", "running", "completed", "failed", "skipped"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: float | None = None
    channel: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Word":
        return cls(str(data.get("text", "")), float(data.get("start", 0)),
                   float(data.get("end", data.get("start", 0))), data.get("probability"),
                   data.get("channel"))


@dataclass
class Segment:
    start: float
    end: float
    ja: str = ""
    zh: str = ""
    speaker: str | None = None
    confidence: float | None = None
    id: str = ""
    channel: str | None = None
    words: list[Word] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["words"] = [word.to_dict() for word in self.words]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Segment":
        return cls(float(data.get("start", 0)), float(data.get("end", data.get("start", 0))),
                   str(data.get("ja", data.get("text", ""))),
                   str(data.get("zh", data.get("translation", ""))), data.get("speaker"),
                   data.get("confidence"), str(data.get("id", "")), data.get("channel"),
                   [Word.from_dict(item) for item in data.get("words", [])],
                   dict(data.get("metadata", {})))


@dataclass
class StageRecord:
    status: StageStatus = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    attempts: int = 0
    artifacts: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None


@dataclass
class TaskManifest:
    task_id: str
    audio_path: str
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    current_stage: str = "created"
    input_sha256: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, StageRecord] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stages"] = {name: asdict(record) for name, record in self.stages.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskManifest":
        stages = {name: StageRecord(**record) for name, record in data.get("stages", {}).items()}
        return cls(data["task_id"], data.get("audio_path", ""), data.get("created_at", utc_now()),
                   data.get("updated_at", utc_now()), data.get("current_stage", "created"),
                   data.get("input_sha256"), dict(data.get("config", {})), stages,
                   list(data.get("warnings", [])), dict(data.get("provenance", {})))


@dataclass
class Task:
    """Legacy-compatible task container; new code should use TaskManifest."""
    id: str
    audio: str
    stage: str = "created"
    segments: list[Segment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["segments"] = [segment.to_dict() for segment in self.segments]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(str(data["id"] if "id" in data else data["task_id"]), data.get("audio", data.get("audio_path", "")),
                   data.get("stage", data.get("current_stage", "created")), [Segment.from_dict(item) for item in data.get("segments", [])],
                   dict(data.get("metadata", {})))


def dumps(value: Any) -> str:
    return json.dumps(value.to_dict() if hasattr(value, "to_dict") else value, ensure_ascii=False, indent=2)
