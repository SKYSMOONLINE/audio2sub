import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core import Pipeline, Segment, Task, TaskStore, Word
from core.gemini_protocol import build_translation_request, apply_translation_response
from core.quality import assess_segments
from core.subtitles import build_outputs, render_srt, render_vtt, save_subtitles


class TemporaryCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


class SubtitleTests(TemporaryCase):
    def test_srt_milliseconds_and_hour_rollover(self):
        self.assertEqual(render_srt(iter([Segment(3599.9996, 3601.234, "JA")])),
                         "1\n01:00:00,000 --> 01:00:01,234\nJA\n")

    def test_vtt_dot_milliseconds(self):
        self.assertEqual(render_vtt([Segment(.123, 1.456, "JA")]),
                         "WEBVTT\n\n00:00:00.123 --> 00:00:01.456\nJA\n")

    def test_empty_iterators(self):
        self.assertEqual(render_srt(iter([])), "")
        self.assertEqual(render_vtt(iter([])), "WEBVTT\n\n")
        self.assertEqual(build_outputs(iter([])), [])

    def test_all_modes_and_iterators(self):
        segment = Segment(0, 1, "JA", "\u55ef\uff0c\u4f60\u597d", speaker="A")
        expected = {"ja": "A\uff1aJA", "zh": "A\uff1a\u55ef\uff0c\u4f60\u597d",
                    "bilingual": "A\uff1a\u55ef\uff0c\u4f60\u597d\uff08JA\uff09", "zh_clean": "A\uff1a\u4f60\u597d"}
        for mode, text in expected.items():
            with self.subTest(mode=mode):
                paths = save_subtitles(iter([segment]), self.root / mode, mode=mode)
                for kind in ("lrc", "srt", "vtt"):
                    self.assertIn(text, paths[kind].read_text(encoding="utf-8"))
                self.assertEqual(json.loads(paths["json"].read_text(encoding="utf-8")), [segment.to_dict()])

    def test_clean_drops_empty_and_renumbers(self):
        items = [Segment(0, 1, "JA", "\u55ef", speaker="A"), Segment(1, 2, "JA", "OK")]
        self.assertEqual(render_srt(items, "zh_clean"), "1\n00:00:01,000 --> 00:00:02,000\nOK\n")
        self.assertEqual(len(build_outputs(items, "zh_clean")), 1)
        self.assertNotIn("A", render_vtt(items, "zh_clean"))

    def test_fallback_and_row_compatibility(self):
        for row in ((0, 1, "JA", ""), (0, 1, "JA", "", None), Segment(0, 1, "JA").to_dict()):
            for mode in ("ja", "zh", "bilingual"):
                self.assertIn("JA", render_srt([row], mode))
            self.assertEqual(render_srt([row], "zh_clean"), "")

    def test_invalid_modes_rejected_even_empty(self):
        for renderer in (render_srt, render_vtt, build_outputs):
            with self.assertRaises(ValueError):
                renderer([], "bad")
        with self.assertRaises(ValueError):
            save_subtitles([], self.root, mode="bad")


class StorageTests(TemporaryCase):
    def test_legacy_roundtrip_and_update(self):
        store = TaskStore(self.root)
        task = Task("legacy", "voice.wav", segments=[Segment(0, 1, "JA", id="a")], metadata={"x": [1]})
        store.save(task)
        store.update_stage("legacy", "done")
        task.stage = "done"
        self.assertEqual(store.load("legacy"), task)
        self.assertFalse(store.manifest_path("legacy").exists())

    def test_manifest_load_and_mixed_update(self):
        store = TaskStore(self.root)
        store.create("new", "voice.wav")
        self.assertEqual(store.load("new").id, "new")
        store.update_stage("new", "ready")
        self.assertEqual(store.load("new").stage, "ready")
        self.assertEqual(store.load_manifest("new").current_stage, "ready")
        task = store.load("new")
        task.segments = [Segment(0, 1, "JA")]
        task.metadata = {"keep": True}
        store.save(task)
        store.update_stage("new", "done")
        self.assertEqual(store.load("new").segments, task.segments)
        self.assertEqual(store.load("new").metadata, task.metadata)

    def test_manifest_shaped_legacy_file(self):
        store = TaskStore(self.root)
        manifest = store.create("oldbug", "voice.wav")
        store._atomic_json(store.path("oldbug"), manifest.to_dict())
        store.update_stage("oldbug", "fixed")
        self.assertEqual(store.load("oldbug").stage, "fixed")

    def test_hash_streams_without_read_bytes(self):
        audio = self.root / "audio.wav"
        content = b"abc123" * 400000
        audio.write_bytes(content)
        store = TaskStore(self.root / "tasks")
        store.create("hash", audio)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("not streaming")):
            self.assertEqual(store.set_input_hash("hash").input_sha256, hashlib.sha256(content).hexdigest())

    def test_stage_retry_clears_error_and_artifacts(self):
        store = TaskStore(self.root)
        store.create("task", "audio.wav")
        store.begin_stage("task", "work")
        store.fail_stage("task", "work", {"message": "failed"})
        record = store.begin_stage("task", "work").stages["work"]
        self.assertEqual((record.status, record.attempts), ("running", 2))
        self.assertIsNone(record.error)
        self.assertIsNone(record.finished_at)
        self.assertEqual(record.artifacts, [])
        record = store.finish_stage("task", "work", []).stages["work"]
        self.assertEqual(record.status, "completed")
        self.assertIsNotNone(record.started_at)
        self.assertIsNotNone(record.finished_at)


class QualityTests(unittest.TestCase):
    def test_nonfinite_negative_and_reversed_times(self):
        for start, end in ((math.nan, 1), (0, math.inf), (-1, 1), (0, -1), (2, 1), (1, 1), (-math.inf, 1)):
            with self.subTest(start=start, end=end):
                report = assess_segments([Segment(start, end, "JA", id="a")])
                self.assertEqual(report["issues"][0]["issue"], "invalid_time")
                self.assertEqual(report["status"], "warning")
                json.dumps(report, allow_nan=False)

    def test_empty_text_reports_warning(self):
        for text in ("", " \n", None):
            report = assess_segments([Segment(0, 1, text)])
            self.assertEqual(report["missing_text_count"], 1)
            self.assertEqual(report["issues"][0]["issue"], "missing_text")
            self.assertEqual(report["status"], "warning")

    def test_short_overlap_and_no_calibrated_score(self):
        report = assess_segments(iter([Segment(0, 1, "A"), Segment(.5, .51, "B")]))
        self.assertEqual({i["issue"] for i in report["issues"]}, {"short_segment", "overlap"})
        self.assertIsNone(report["score"])
        self.assertIn("not_calibrated", report["assessment"])

    def test_success_and_empty(self):
        for items in ([], [Segment(0, 1, "JA")]):
            report = assess_segments(items)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["issues"], [])
            self.assertIsNone(report["score"])


class ImportAndPackagingTests(unittest.TestCase):
    def test_core_import_does_not_mutate_environment(self):
        root = Path(__file__).resolve().parents[1]
        code = "import os, sys; before=dict(os.environ); import core; import core.diagnostics; assert dict(os.environ)==before; assert 'config' not in sys.modules"
        result = subprocess.run([sys.executable, "-c", code], cwd=root, env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_packaging_includes_config(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('[tool.setuptools]\npy-modules = ["config"]', text)


class PipelineTests(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.pipeline = Pipeline(self.root / "tasks")
        self.pipeline.create("task", self.root / "audio.wav")
        self.items = [Segment(0, 1, "JA", id="a")]
        self.response = self.root / "response.json"

    def record(self, stage):
        return self.pipeline.store.load_manifest("task").stages[stage]

    def test_success_flow_and_running_records(self):
        from core.pipeline import write_translation_request
        def observe(*args, **kwargs):
            self.assertEqual(self.record("prepare_translation").status, "running")
            return write_translation_request(*args, **kwargs)
        with patch("core.pipeline.write_translation_request", side_effect=observe):
            self.pipeline.prepare_translation("task", self.items)
        raw = b'{ "segments": [{"id":"a", "zh":"OK"}] }\r\n'
        self.response.write_bytes(raw)
        result, review = self.pipeline.import_translation("task", self.items, self.response)
        report, queue, paths = self.pipeline.quality_and_export("task", iter(result))
        self.assertEqual((review, queue, report["status"]), ([], [], "ok"))
        self.assertEqual(self.items[0].zh, "")
        for stage in ("prepare_translation", "import_translation", "quality", "export"):
            record = self.record(stage)
            self.assertEqual((record.status, record.attempts), ("completed", 1))
            self.assertTrue(record.started_at <= record.finished_at)
            self.assertIsNone(record.error)
            for artifact in record.artifacts:
                self.assertTrue((self.pipeline.store.task_dir("task") / artifact).exists())
        archive = self.pipeline.store.task_dir("task") / "logs/translation_response.1.raw.json"
        self.assertEqual(archive.read_bytes(), raw)
        self.assertEqual(set(paths), {"json", "lrc", "srt", "vtt"})

    def test_reviews_survive_repeated_quality(self):
        self.response.write_text('{"segments": []}', encoding="utf-8")
        result, review = self.pipeline.import_translation("task", self.items, self.response)
        result[0].ja = ""
        _, first, _ = self.pipeline.quality_and_export("task", result)
        _, second, _ = self.pipeline.quality_and_export("task", result)
        self.assertEqual(first, second)
        self.assertTrue(all(issue in second for issue in review))
        self.assertEqual(len(second), 2)

    def test_legacy_review_queue_survives(self):
        extra = [{"id": "a", "issue": "missing_translation", "severity": "high"}]
        self.pipeline.store.save_artifact("task", "review_queue.json", extra)
        for _ in range(2):
            _, queue, _ = self.pipeline.quality_and_export("task", self.items)
            self.assertEqual(queue, extra)

    def test_invalid_json_archived_failure_and_retry(self):
        self.response.write_bytes(b"{bad\r\n")
        with self.assertRaises(json.JSONDecodeError):
            self.pipeline.import_translation("task", self.items, self.response)
        record = self.record("import_translation")
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error["type"], "JSONDecodeError")
        archived = self.pipeline.store.task_dir("task") / record.artifacts[0]
        self.assertEqual(archived.read_bytes(), b"{bad\r\n")
        self.response.write_text('{"segments": [{"id":"a","zh":"OK"}]}', encoding="utf-8")
        self.pipeline.import_translation("task", self.items, self.response)
        self.assertEqual(self.record("import_translation").attempts, 2)
        self.assertIsNone(self.record("import_translation").error)
        self.assertEqual(archived.read_bytes(), b"{bad\r\n")

    def test_prepare_failure_recorded(self):
        with self.assertRaises(ValueError):
            self.pipeline.prepare_translation("task", [Segment(0, 1, "JA")])
        self.assertEqual(self.record("prepare_translation").status, "failed")
        self.assertIsNotNone(self.record("prepare_translation").finished_at)

    def test_quality_failure_does_not_begin_export(self):
        with patch("core.pipeline.assess_segments", side_effect=RuntimeError("quality failed")):
            with self.assertRaises(RuntimeError):
                self.pipeline.quality_and_export("task", self.items)
        self.assertEqual(self.record("quality").status, "failed")
        self.assertNotIn("export", self.pipeline.store.load_manifest("task").stages)

    def test_export_failure_keeps_quality_results(self):
        with patch("core.pipeline.save_subtitles", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.pipeline.quality_and_export("task", self.items)
        self.assertEqual(self.record("quality").status, "completed")
        self.assertEqual(self.record("export").status, "failed")
        self.assertEqual(self.record("export").error["type"], "OSError")
        self.assertTrue((self.pipeline.store.task_dir("task") / "quality.json").exists())

    def test_invalid_times_reported_before_export_failure(self):
        with self.assertRaises(ValueError):
            self.pipeline.quality_and_export("task", [Segment(math.nan, 1, "JA")])
        self.assertEqual(self.record("quality").status, "completed")
        self.assertEqual(self.record("export").status, "failed")
        report = json.loads((self.pipeline.store.task_dir("task") / "quality.json").read_text())
        self.assertEqual(report["issues"][0]["issue"], "invalid_time")


class ProtocolTests(unittest.TestCase):
    def test_source_ids_required_and_unique(self):
        for ids in (("",), (" ",), (None,), ("a", "a")):
            for operation in (build_translation_request, lambda s: apply_translation_response(s, {})):
                with self.subTest(ids=ids), self.assertRaises(ValueError):
                    operation(Segment(0, 1, "JA", id=sid) for sid in ids)

    def test_request_context_and_metadata_are_detached(self):
        items = [Segment(0, 1, "A", id="a", metadata={"nested": []}), Segment(1, 2, "B", id="b")]
        request = build_translation_request(iter(items), context_window=1)
        self.assertEqual(request["segments"][0]["context_after"], [{"id": "b", "ja": "B"}])
        self.assertEqual(request["segments"][1]["context_before"], [{"id": "a", "ja": "A"}])
        request["segments"][0]["metadata"]["nested"].append(1)
        self.assertEqual(items[0].metadata, {"nested": []})

    def test_response_preserves_order_and_inputs(self):
        items = [Segment(0, 1, "A", id="a", words=[Word("A", 0, 1)], metadata={"old": []}), Segment(1, 2, "B", id="b")]
        response = {"segments": [{"id": "b", "zh": "B2"}, {"id": "a", "translation": "A2", "metadata": {"new": []}}]}
        before, response_before = copy.deepcopy(items), copy.deepcopy(response)
        result, review = apply_translation_response(iter(items), response)
        self.assertEqual([s.id for s in result], ["a", "b"])
        self.assertEqual([s.zh for s in result], ["A2", "B2"])
        self.assertEqual(review, [])
        result[0].metadata["old"].append(1)
        result[0].metadata["new"].append(2)
        result[0].words[0].text = "changed"
        self.assertEqual(items, before)
        self.assertEqual(response, response_before)

    def test_response_reports_all_translation_faults(self):
        items = [Segment(0, 1, "JA", id=sid) for sid in ("a", "b", "c")]
        rows = [{"id": "a", "zh": "first"}, {"id": "a", "zh": "second"},
                {"id": "b", "zh": None}, {"id": "unknown", "zh": "X"}, {"id": ""}, 42]
        result, review = apply_translation_response(items, {"segments": rows})
        self.assertEqual(result[0].zh, "first")
        self.assertEqual({r["issue"] for r in review}, {"duplicate_segment_id", "empty_translation", "unknown_segment_id", "empty_segment_id", "invalid_translation_row", "missing_translation"})
        self.assertEqual([s.id for s in result], ["a", "b", "c"])

    def test_whitespace_translation_and_invalid_response(self):
        for text in ("", "  ", None, 123):
            _, review = apply_translation_response([Segment(0, 1, "A", id="a")], {"translations": [{"id": "a", "text": text}]})
            self.assertEqual(review[0]["issue"], "empty_translation")
        for response in (None, [], {"segments": "wrong"}):
            _, review = apply_translation_response([Segment(0, 1, "A", id="a")], response)
            self.assertEqual({r["issue"] for r in review}, {"invalid_response", "missing_translation"})
