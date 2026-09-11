"""Deterministic checks, not calibrated transcription or alignment scores."""
from __future__ import annotations
import math
from typing import Any, Iterable


def assess_segments(segments: Iterable[Any], *, min_duration: float = 0.08) -> dict[str, Any]:
    items = list(segments)
    issues = []
    total = previous_end = 0.0
    short = missing = overlaps = 0
    for index, item in enumerate(items):
        start, end = float(item.start), float(item.end)
        sid = getattr(item, "id", f"seg-{index:06d}")
        valid = math.isfinite(start) and math.isfinite(end) and 0 <= start < end
        if not valid:
            issues.append({"id": sid, "issue": "invalid_time", "severity": "high"})
        else:
            total = max(total, end)
            if end - start < min_duration:
                short += 1
                issues.append({"id": sid, "issue": "short_segment", "severity": "medium"})
            if start < previous_end:
                overlaps += 1
                issues.append({"id": sid, "issue": "overlap", "severity": "medium"})
            previous_end = max(previous_end, end)
        text = getattr(item, "ja", "")
        if not isinstance(text, str) or not text.strip():
            missing += 1
            issues.append({"id": sid, "issue": "missing_text", "severity": "high"})
    return {"status": "warning" if issues else "ok", "score": None,
            "assessment": "deterministic_checks_not_calibrated", "segment_count": len(items),
            "duration": total, "short_segment_count": short, "missing_text_count": missing,
            "overlap_count": overlaps, "issues": issues}


def build_review_queue(report: dict[str, Any], extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return list(report.get("issues", [])) + list(extra or [])
