# -*- coding: utf-8 -*-
"""
听译模块：音频 -> 带时间戳的文本段
- faster-whisper 本地 GPU/CPU 推理
- 注入领域提示词（initial_prompt）提升音声作品识别率
- 从配置的模型注册表解析本地路径
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from difflib import SequenceMatcher
try:
    import numpy as np
except ImportError:
    np = None

# Whisper 模型需要文件：本地目录里必须含 model.bin + config.json + tokenizer.json
_REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")


def resolve_model_path(model_name: str) -> str:
    """把注册名解析为本地目录路径；tiny 的 snapshot 目录名是 hash，需自动查找子目录。"""
    from config import MODEL_REGISTRY
    base = MODEL_REGISTRY.get(model_name)
    if not base:
        raise RuntimeError(f"模型 '{model_name}' 不在注册表中")
    p = Path(base)
    # 若路径指向 snapshot 根（含 hash 子目录），取第一个子目录
    if p.is_dir() and not (p / "model.bin").exists():
        subs = [x for x in p.iterdir() if x.is_dir()]
        for s in subs:
            if (s / "model.bin").exists():
                return str(s)
        raise RuntimeError(f"模型目录 {p} 里找不到 model.bin（模型未完整下载？）")
    if not (p / "model.bin").exists():
        raise RuntimeError(f"模型文件不存在: {p}（需先下载模型）")
    return str(p)


def load_model(model_name: str, compute_type: str = "int8_float16", device: str = "cuda", log=None):
    """加载 Whisper 模型；GPU 不可用时自动回退 CPU。

    - device="cuda": 优先 GPU；加载失败会打日志说明原因，再回退 CPU（不静默）。
    - device="cpu": 直接加载 CPU 版（供显式指定/无卡机器）。
    返回带 .device 标注的模型（faster-whisper 本身不暴露 device，这里补上便于日志/状态显示）。
    """
    _log = log if callable(log) else (lambda msg: None)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("ASR requires faster-whisper; install requirements.txt (no model download is performed)") from exc
    path = resolve_model_path(model_name)
    if device == "cpu":
        _log("按配置使用 CPU（int8）…")
        model = WhisperModel(path, device="cpu", compute_type="int8")
        model.device = "cpu"
        return model
    try:
        _log("尝试加载 CUDA GPU（%s）…" % compute_type)
        model = WhisperModel(path, device="cuda", compute_type=compute_type)
        model.device = "cuda"
        return model
    except Exception as e:
        # GPU 加载失败（驱动/显存/DLL 等）：记录原因后回退 CPU，不静默吞掉
        _log(f"⚠ CUDA 加载失败（{type(e).__name__}: {e}），回退 CPU int8")
        try:
            model = WhisperModel(path, device="cpu", compute_type="int8")
            model.device = "cpu"
            return model
        except Exception as e2:
            raise RuntimeError(f"CPU 回退也失败: {e2}") from e


# ---------- 音频声学前处理（DSP 滤波与弱音自适应增益） ----------

def highpass_and_normalize(audio: np.ndarray, sr: int = 16000, cutoff: float = 80.0) -> np.ndarray:
    """
    纯 NumPy 极速音频前处理：
    1. 65 阶 FIR 高通滤波（~80Hz）：滤除 ASMR 贴麦录音中 <80Hz 的气流喷麦与低频爆破轰鸣；
    2. 温和峰值自适应增益：适度抬升极微弱耳语与气声的电平，防止 Whisper 漏词。
    """
    if np is None:
        raise RuntimeError("ASR audio preprocessing requires numpy")
    if len(audio) < 65:
        return audio
    fc = cutoff / sr
    N = 65
    n = np.arange(N) - (N - 1) / 2
    h = -np.sinc(2 * fc * n)
    h[(N - 1) // 2] += 1.0
    h *= np.hamming(N)
    sum_h = np.sum(np.abs(h))
    if sum_h > 0:
        h /= sum_h

    filtered = np.convolve(audio, h, mode="same")
    max_val = np.max(np.abs(filtered))
    if max_val > 1e-4:
        gain = min(0.9 / max_val, 3.0)
        return np.clip(filtered * gain, -1.0, 1.0).astype(np.float32)
    return filtered.astype(np.float32)


# ---------- 双声道立体声片段智能合并 ----------

def merge_stereo_segments(segs_l, segs_r, time_tol=1.2):
    """
    合并左耳与右耳独立识别出的片段：
    - 时间相近（|start_l - start_r| < time_tol）且文字相似度 >= 0.7（同一人说话）：合并去重
    - 时间相近但文字不同（双人同时在左右耳说话）：分别保留，并标注【左耳】/【右耳】
    - 仅某一侧有声：保留并打上对应声道标签
    - 按起始时间戳重新排序
    """
    combined = []
    matched_r = set()

    for sl, el, tl in segs_l:
        match_found = False
        for j, (sr, er, tr) in enumerate(segs_r):
            if j in matched_r:
                continue
            if abs(sl - sr) <= time_tol:
                ratio = SequenceMatcher(None, tl, tr).ratio()
                if ratio >= 0.7:
                    best_text = tl if len(tl) >= len(tr) else tr
                    combined.append((min(sl, sr), max(el, er), best_text))
                    matched_r.add(j)
                    match_found = True
                    break
                else:
                    combined.append((sl, el, f"【左耳】{tl}"))
                    combined.append((sr, er, f"【右耳】{tr}"))
                    matched_r.add(j)
                    match_found = True
                    break
        if not match_found:
            combined.append((sl, el, f"【左耳】{tl}"))

    for j, (sr, er, tr) in enumerate(segs_r):
        if j not in matched_r:
            combined.append((sr, er, f"【右耳】{tr}"))

    combined.sort(key=lambda x: x[0])
    return combined


# 默认 VAD 参数（专为 ASMR/音声优化：降低静音阈值保护微弱耳语与气声，前后留缓冲 padding）
DEFAULT_VAD_PARAMS = {
    "threshold": 0.35,
    "min_speech_duration_ms": 250,
    "max_speech_duration_s": float("inf"),
    "min_silence_duration_ms": 700,
    "speech_pad_ms": 400,
}


def transcribe_audio(model, audio_path, language="ja", initial_prompt="",
                     beam_size=5, progress_cb=None,
                     condition_on_previous_text=False,
                     vad_filter=False, vad_parameters=None,
                     preprocess_audio=True, split_stereo=False):
    """
    转录音频，返回片段列表 [(start, end, text), ...]
    - condition_on_previous_text=False: 避免耳语/空白出现幻觉时发生连锁复读
    - vad_filter: ASMR 场景中因含有大量微弱耳语/气声，默认 False 防误杀；可按需开启
    - preprocess_audio=True: 80Hz 高通滤波消除贴麦喷气，弱音自适应增益
    - split_stereo: 双人声/双耳立体声模式（分别独立解码左右耳并交错合并）
    """
    audio_path = str(audio_path)
    try:
        from faster_whisper.audio import decode_audio
    except ImportError as exc:
        raise RuntimeError("ASR requires faster-whisper audio decoder") from exc
    _log = progress_cb if callable(progress_cb) else (lambda msg: None)
    vad_params = vad_parameters if vad_parameters is not None else DEFAULT_VAD_PARAMS

    if split_stereo:
        _log("解码双耳立体声音频（拆分左声道与右声道）…")
        decoded = decode_audio(audio_path, sampling_rate=16000, split_stereo=True)
        if isinstance(decoded, (tuple, list)) and len(decoded) == 2:
            wav_l, wav_r = decoded
            if preprocess_audio:
                _log("音频声学前处理（左耳与右耳 80Hz 高通滤波 + 动态增益）…")
                wav_l = highpass_and_normalize(wav_l)
                wav_r = highpass_and_normalize(wav_r)

            _log("识别左耳声道中…")
            segs_l_raw, info_l = model.transcribe(
                wav_l, language=None if language == "auto" else language,
                beam_size=beam_size, initial_prompt=initial_prompt or None,
                word_timestamps=True,
                condition_on_previous_text=condition_on_previous_text,
                vad_filter=vad_filter, vad_parameters=vad_params,
                compression_ratio_threshold=2.4,
            )
            segs_l = [
                (
                    s.words[0].start if (hasattr(s, "words") and s.words) else s.start,
                    s.words[-1].end if (hasattr(s, "words") and s.words) else s.end,
                    s.text.strip()
                )
                for s in segs_l_raw
                if getattr(s, "no_speech_prob", 0.0) <= 0.75 and s.text.strip()
            ]

            _log("识别右耳声道中…")
            segs_r_raw, info_r = model.transcribe(
                wav_r, language=None if language == "auto" else language,
                beam_size=beam_size, initial_prompt=initial_prompt or None,
                word_timestamps=True,
                condition_on_previous_text=condition_on_previous_text,
                vad_filter=vad_filter, vad_parameters=vad_params,
                compression_ratio_threshold=2.4,
            )
            segs_r = [
                (
                    s.words[0].start if (hasattr(s, "words") and s.words) else s.start,
                    s.words[-1].end if (hasattr(s, "words") and s.words) else s.end,
                    s.text.strip()
                )
                for s in segs_r_raw
                if getattr(s, "no_speech_prob", 0.0) <= 0.75 and s.text.strip()
            ]

            _log("合并双耳声道台词（交错对齐与去重）…")
            segs = merge_stereo_segments(segs_l, segs_r)
            lang = getattr(info_l, "language", language)
            _log(f"双耳识别完成：左耳 {len(segs_l)} 片段，右耳 {len(segs_r)} 片段，合并后 {len(segs)} 片段")
            return segs, lang

    # 单声道或常规立体声处理
    _log("识别中（音频时长决定耗时）…")
    if preprocess_audio:
        wav = decode_audio(audio_path, sampling_rate=16000, split_stereo=False)
        wav = highpass_and_normalize(wav)
        input_data = wav
    else:
        input_data = audio_path

    segments, info = model.transcribe(
        input_data,
        language=None if language == "auto" else language,
        beam_size=beam_size,
        word_timestamps=True,
        initial_prompt=initial_prompt or None,
        condition_on_previous_text=condition_on_previous_text,
        vad_filter=vad_filter,
        vad_parameters=vad_params,
        compression_ratio_threshold=2.4,
    )
    segs = []
    for s in segments:
        if getattr(s, "no_speech_prob", 0.0) > 0.75:
            continue
        txt = s.text.strip()
        if not txt:
            continue
        start = s.words[0].start if (hasattr(s, "words") and s.words) else s.start
        end = s.words[-1].end if (hasattr(s, "words") and s.words) else s.end
        segs.append((start, end, txt))
    lang = getattr(info, "language", language)
    prob = getattr(info, "language_probability", 0)
    _log(f"识别完成：语言 {lang}（置信度 {prob:.2f}），{len(segs)} 个片段")
    return segs, lang
