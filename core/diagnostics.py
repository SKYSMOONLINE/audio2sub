"""Runtime environment diagnostics."""
from __future__ import annotations
import importlib.util
import platform
import sys
from pathlib import Path
from typing import Any

def diagnose_environment() -> dict[str, Any]:
    import config
    model = config.MODEL_REGISTRY.get(config.DEFAULT_MODEL, "")
    model_path = Path(model)
    required = ["model.bin", "config.json", "tokenizer.json"]
    return {"python": sys.version.split()[0], "platform": platform.platform(), "faster_whisper": importlib.util.find_spec("faster_whisper") is not None, "ctranslate2": importlib.util.find_spec("ctranslate2") is not None, "default_model": config.DEFAULT_MODEL, "model_path": str(model_path), "model_exists": model_path.is_dir(), "model_files": {name: (model_path / name).is_file() for name in required}, "compute_type": config.DEFAULT_COMPUTE}
