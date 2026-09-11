import json
import tempfile
import unittest
from pathlib import Path

from core import Pipeline, Segment, Word


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / 'input.wav'
        self.audio.write_bytes(b'test input')
        self.pipeline = Pipeline(Path(self.temp.name) / 'tasks')
        self.pipeline.create('test', self.audio)

    def test_injected_asr_persists_outputs(self):
        calls = []
        def asr(path, **options):
            calls.append((path, options))
            return [(0, 1, 'hello')], 'ja'
        result = self.pipeline.transcribe('test', asr=asr, language='ja')
        self.assertEqual(len(calls), 1)
        self.assertEqual(result[0].id, 'seg-000000')
        directory = self.pipeline.store.task_dir('test')
        self.assertEqual(json.loads((directory / 'transcript.raw.json').read_text())['language'], 'ja')
        self.assertTrue((directory / 'transcript.ja.json').exists())
        manifest = self.pipeline.store.load_manifest('test')
        self.assertEqual(manifest.stages['transcribe'].status, 'completed')
        self.assertTrue(manifest.input_sha256)

    def test_metadata_is_copied(self):
        original = Segment(0, 1, 'hello', words=[Word('hello', 0, 1)], channel='L')
        result = self.pipeline.transcribe('test', asr=lambda path: ([original], 'ja'))
        self.assertEqual(original.id, '')
        self.assertEqual(result[0].channel, 'L')
        self.assertIsNot(result[0].words[0], original.words[0])

    def test_failure_is_recorded(self):
        def fail(path):
            raise RuntimeError('ASR failed')
        with self.assertRaisesRegex(RuntimeError, 'ASR failed'):
            self.pipeline.transcribe('test', asr=fail)
        self.assertEqual(self.pipeline.store.load_manifest('test').stages['transcribe'].status, 'failed')

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.pipeline.transcribe('test', asr=lambda path: ([Segment(0, 1, id='same'), Segment(1, 2, id='same')], 'ja'))

    def test_unimplemented_operations_are_not_exposed(self):
        self.assertFalse(hasattr(self.pipeline, 'recover'))
        self.assertFalse(hasattr(self.pipeline, 'rerun_range'))


if __name__ == '__main__':
    unittest.main()
