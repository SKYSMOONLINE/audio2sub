"""High-level orchestration primitives for Antigravity tasks."""
from __future__ import annotations
import json
import copy
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .models import Segment
from .storage import TaskStore
from .gemini_protocol import write_translation_request, read_translation_response, apply_translation_response
from .quality import assess_segments, build_review_queue
from .subtitles import save_subtitles

class Pipeline:
    def __init__(self, tasks_root: str | Path):
        self.store = TaskStore(tasks_root)

    def create(self, task_id: str, audio_path: str | Path, config: dict[str, Any] | None = None):
        return self.store.create(task_id, audio_path, config)

    @contextmanager
    def _stage(self, task_id: str, stage: str):
        self.store.begin_stage(task_id, stage)
        artifacts = []
        try:
            yield artifacts
            self.store.finish_stage(task_id, stage, artifacts)
        except Exception as exc:
            self.store.fail_stage(task_id, stage, {"type": type(exc).__name__, "message": str(exc)})
            raise

    def prepare_translation(self, task_id: str, segments: list[Segment], **kwargs: Any) -> Path:
        with self._stage(task_id, "prepare_translation") as artifacts:
            path = self.store.task_dir(task_id) / "translation_request.json"
            write_translation_request(path, segments, audio_path=self.store.load_manifest(task_id).audio_path, **kwargs)
            artifacts.append(path.name)
        return path

    def transcribe(self, task_id: str, *, model=None, asr=None, **kwargs):
        """Run ASR; injected callbacks return (segments, language)."""
        with self._stage(task_id, "transcribe") as artifacts:
            manifest = self.store.load_manifest(task_id)
            if asr is None:
                from .transcribe import transcribe_audio, load_model
                if model is None:
                    model = load_model(kwargs.pop("model_name", manifest.config.get("model", "large-v2")), device=kwargs.pop("device", "cpu"))
                rows, language = transcribe_audio(model, manifest.audio_path, **kwargs)
            else:
                rows, language = asr(manifest.audio_path, **kwargs)
            segments = [copy.deepcopy(r) if isinstance(r, Segment) else Segment(float(r[0]), float(r[1]), str(r[2])) for r in rows]
            for i, segment in enumerate(segments):
                segment.id = segment.id or f"seg-{i:06d}"
            if len({segment.id for segment in segments}) != len(segments):
                raise ValueError("duplicate ASR segment IDs")
            self.store.save_artifact(task_id, "transcript.ja.json", [s.to_dict() for s in segments])
            self.store.save_artifact(task_id, "transcript.raw.json", {"language": language, "segments": [s.to_dict() for s in segments]})
            self.store.set_input_hash(task_id)
            artifacts.extend(["transcript.ja.json", "transcript.raw.json"])
        return segments

    def translation_status(self, task_id: str):
        record = self.store.load_manifest(task_id).stages.get("import_translation")
        return {"status": "ready" if record and record.status == "completed" and (self.store.task_dir(task_id) / "transcript.zh.json").is_file() else "pending"}

    def import_translation(self, task_id: str, segments: list[Segment], response_path: str | Path):
        with self._stage(task_id, "import_translation") as artifacts:
            attempt = self.store.load_manifest(task_id).stages["import_translation"].attempts
            raw_name = f"logs/translation_response.{attempt}.raw.json"
            raw_path = self.store.task_dir(task_id) / raw_name
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(Path(response_path).read_bytes())
            # Record the raw artifact before parsing, including failed attempts.
            manifest = self.store.load_manifest(task_id)
            manifest.stages["import_translation"].artifacts = [raw_name]
            self.store.save_manifest(manifest)
            artifacts.append(raw_name)
            result, review = apply_translation_response(segments, read_translation_response(raw_path))
            self.store.save_artifact(task_id, "transcript.zh.json", [x.to_dict() for x in result])
            self.store.save_artifact(task_id, "translation_review.json", review)
            self.store.save_artifact(task_id, "review_queue.json", review)
            artifacts.extend(["transcript.zh.json", "translation_review.json", "review_queue.json"])
        return result, review

    def quality_and_export(self, task_id: str, segments: list[Segment], mode: str = "zh"):
        with self._stage(task_id, "quality") as artifacts:
            segments = list(segments)
            report = assess_segments(segments)
            review_path = self.store.task_dir(task_id) / "translation_review.json"
            if not review_path.exists():
                review_path = self.store.task_dir(task_id) / "review_queue.json"
                old_queue = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else []
                quality_path = self.store.task_dir(task_id) / "quality.json"
                old_issues = json.loads(quality_path.read_text(encoding="utf-8")).get("issues", []) if quality_path.exists() else []
                extra = [issue for issue in old_queue if issue not in old_issues]
            else:
                extra = json.loads(review_path.read_text(encoding="utf-8"))
            queue = build_review_queue(report, extra)
            self.store.save_artifact(task_id, "quality.json", report)
            self.store.save_artifact(task_id, "review_queue.json", queue)
            artifacts.extend(["quality.json", "review_queue.json"])
        with self._stage(task_id, "export") as artifacts:
            if any(issue["issue"] == "invalid_time" for issue in report["issues"]):
                raise ValueError("cannot export invalid subtitle times; see quality.json")
            paths = save_subtitles(segments, self.store.task_dir(task_id) / "exports", mode=mode)
            artifacts.extend(str(p.relative_to(self.store.task_dir(task_id))) for p in paths.values())
        return report, queue, paths
