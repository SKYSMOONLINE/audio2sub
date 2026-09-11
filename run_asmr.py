# -*- coding: utf-8 -*-
"""
ASMR CLI Entrypoint
Unified top-level entrypoint delegating to audio2sub.run_album.
"""
import os
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# cuBLAS DLL auto-injection
_CUBLAS_CANDIDATES = [
    Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
    Path(sys.prefix) / "lib" / "site-packages" / "nvidia" / "cublas" / "bin",
    Path(r"D:/ASMR/openlrc/.venv/Lib/site-packages/nvidia/cublas/bin"),
]
_CUBLAS_BIN = next((p for p in _CUBLAS_CANDIDATES if p.is_dir()), None)
if _CUBLAS_BIN:
    os.environ["PATH"] = str(_CUBLAS_BIN) + os.pathsep + os.environ.get("PATH", "")

# Ensure audio2sub and its core package are resolvable in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
for _p in [_PROJECT_ROOT / "audio2sub", _PROJECT_ROOT / "audio2sub" / "core"]:
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from run_album import main

if __name__ == "__main__":
    main()
