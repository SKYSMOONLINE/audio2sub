"""Antigravity/Gemini translation file protocol."""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable
from .models import Segment

def _validated_segments(segments: Iterable[Segment]) -> list[Segment]:
    items = list(segments)
    seen = set()
    for segment in items:
        if not isinstance(segment.id, str) or not segment.id.strip():
            raise ValueError("segment ID must be a non-empty string")
        if segment.id in seen:
            raise ValueError(f"duplicate segment ID: {segment.id}")
        seen.add(segment.id)
    return items

def build_translation_request(segments: Iterable[Segment], *, audio_path: str | None = None, domain_prompt: str = "", context_window: int = 2, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    items = _validated_segments(segments)
    payload: dict[str, Any] = {"protocol": "audio2sub.translation.v1", "instructions": "请结合所有上下文自行听译并翻译；保留片段 ID，不要改变时间轴。", "audio": {"path": audio_path} if audio_path else None, "domain_prompt": domain_prompt, "metadata": metadata or {}, "segments": []}
    for index, segment in enumerate(items):
        sid = segment.id
        payload["segments"].append({"id": sid, "start": segment.start, "end": segment.end, "ja": segment.ja, "speaker": segment.speaker, "channel": segment.channel, "confidence": segment.confidence, "words": [word.to_dict() for word in segment.words], "context_before": [{"id": x.id, "ja": x.ja} for x in items[max(0, index-context_window):index]], "context_after": [{"id": x.id, "ja": x.ja} for x in items[index+1:index+1+context_window]], "metadata": segment.metadata})
    return deepcopy(payload)

def write_translation_request(path: str | Path, segments: Iterable[Segment], **kwargs: Any) -> Path:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_translation_request(segments, **kwargs), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target

def read_translation_response(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def apply_translation_response(segments: Iterable[Segment], response: Any) -> tuple[list[Segment], list[dict[str, Any]]]:
    result = deepcopy(_validated_segments(segments))
    source = {s.id: s for s in result}
    rows = response.get("segments", response.get("translations", [])) if isinstance(response, dict) else None
    review: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(rows, list):
        review.append({"issue": "invalid_response", "severity": "high"})
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            review.append({"issue": "invalid_translation_row", "severity": "high", "raw": deepcopy(row)})
            continue
        sid = row.get("id")
        if not isinstance(sid, str) or not sid.strip():
            review.append({"issue": "empty_segment_id", "severity": "high", "raw": deepcopy(row)})
            continue
        if sid in seen:
            review.append({"id": sid, "issue": "duplicate_segment_id", "severity": "high", "raw": deepcopy(row)})
            continue
        seen.add(sid)
        if sid not in source:
            review.append({"id": sid, "issue": "unknown_segment_id", "severity": "high", "raw": deepcopy(row)})
            continue
        segment = source[sid]
        text = row.get("zh", row.get("translation", row.get("text", "")))
        segment.zh = text if isinstance(text, str) else ""
        if not segment.zh.strip():
            review.append({"id": sid, "issue": "empty_translation", "severity": "high"})
        if row.get("speaker"): segment.speaker = str(row["speaker"])
        if isinstance(row.get("metadata"), dict): segment.metadata.update(deepcopy(row["metadata"]))
    for sid, segment in source.items():
        if sid not in seen: review.append({"id": sid, "issue": "missing_translation", "severity": "high", "ja": segment.ja})
    return result, review
