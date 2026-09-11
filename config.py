# -*- coding: utf-8 -*-
"""
audio2sub 全局配置
- 模型注册表（名称 -> 本地路径）
- 默认参数
"""
import os
import sys
from pathlib import Path

# 软件根目录（此文件所在目录）
APP_DIR = Path(__file__).resolve().parent


def _find_model_dir(name: str, fallback_path: str) -> str:
    """动态查找模型目录：环境变量 -> 本地 models/ -> 兄弟目录 models/ -> 回退路径"""
    env_key = f"WHISPER_MODEL_{name.upper().replace('-', '_')}"
    if env_key in os.environ and Path(os.environ[env_key]).is_dir():
        return os.environ[env_key]
    local_p = APP_DIR / "models" / name
    if local_p.is_dir():
        return str(local_p)
    sibling_p = APP_DIR.parent / "openlrc" / "models" / name
    if sibling_p.is_dir():
        return str(sibling_p)
    return fallback_path


# 模型注册表：所有可用 Whisper 模型的本地路径
MODEL_REGISTRY = {
    "tiny": _find_model_dir("tiny", r"C:/Users/Obilivionis/.cache/huggingface/hub/models--Systran--faster-whisper-tiny/snapshots"),
    "small": _find_model_dir("small", r"C:/Users/Obilivionis/.cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots/536b0662742c02347bc0e980a01041f333bce120"),
    "large-v2": _find_model_dir("large-v2", r"D:/ASMR/openlrc/models/large-v2"),
}


# cuBLAS DLL 目录（Windows 上 faster-whisper GPU 必需），自动注入 PATH
_CUBLAS_CANDIDATES = [
    Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
    Path(sys.prefix) / "lib" / "site-packages" / "nvidia" / "cublas" / "bin",
    Path(r"D:/ASMR/openlrc/.venv/Lib/site-packages/nvidia/cublas/bin"),
]
_CUBLAS_BIN = next((p for p in _CUBLAS_CANDIDATES if p.is_dir()), None)
if _CUBLAS_BIN:
    os.environ["PATH"] = str(_CUBLAS_BIN) + os.pathsep + os.environ.get("PATH", "")

# HuggingFace 镜像（模型下载走国内镜像）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# 支持的音频扩展名
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".mp4", ".mkv", ".webm"}

# ---------- 运行时默认 ----------
DEFAULT_MODEL = "large-v2"
DEFAULT_LANG = "ja"
DEFAULT_COMPUTE = "int8_float16"  # 6G 显存用 int8_float16；显存大/小模型可用 float16

