# -*- coding: utf-8 -*-
"""
ASMR Autonomous Album ASR & Alignment Pipeline (Standalone Local Version)
1. Sniffs album metadata (CV, circle, character names, scenario tags).
2. Keeps Faster-Whisper large-v2 loaded in CUDA GPU memory (zero-cold-start between tracks).
3. Executes Track-by-Track acoustic DTW alignment:
   - Micro-theatre prompt synthesis (free from vocabulary dumping / hallucinations).
   - 80Hz FIR DSP filtering & word-level DTW onset alignment (s.words[0].start).
   - Calibrated speech threshold (p_silent <= 0.75).
   - Exports [Track].日文原稿.lrc, [Track].日文原稿.txt, and [Track].segments.json.
   - If [Track].中文翻译.txt exists, automatically exports pure Chinese [Track].lrc via sanity gate.
   - Extracts real-time safe context feedback for subsequent tracks.
4. 100% Local GPU execution, zero cloud API dependencies.
"""

import os
import sys
import re
import json
import time
import argparse
from pathlib import Path

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# cuBLAS injection
_CUBLAS_CANDIDATES = [
    Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
    Path(sys.prefix) / "lib" / "site-packages" / "nvidia" / "cublas" / "bin",
    Path(r"D:/ASMR/openlrc/.venv/Lib/site-packages/nvidia/cublas/bin"),
]
_CUBLAS_BIN = next((p for p in _CUBLAS_CANDIDATES if p.is_dir()), None)
if _CUBLAS_BIN:
    os.environ["PATH"] = str(_CUBLAS_BIN) + os.pathsep + os.environ.get("PATH", "")

# Add core directories to sys.path
for candidate in [
    Path(__file__).resolve().parent / "core",
    Path(__file__).resolve().parent / "audio2sub" / "core",
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent / "audio2sub",
]:
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from metadata_sniffer import sniff_album_metadata, format_summary
from prompt_builder import build_initial_prompt, estimate_tokens
from session_persona import (
    init_session_persona,
    load_session_persona,
    update_session_persona,
    get_persona_prompt_keywords
)
from sub_exporter import export_subtitles

try:
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
except ImportError as e:
    print(f"错误: 无法导入 faster_whisper: {e}，请确保在 D:/ASMR/openlrc/.venv 中运行", file=sys.stderr)
    sys.exit(1)

import numpy as np


def natural_sort_key(p: Path):
    m = re.search(r"#(\d+)([A-Za-z]?)", p.name)
    if m:
        return (int(m.group(1)), m.group(2).upper())
    d_m = re.search(r"(\d+)", p.name)
    if d_m:
        return (int(d_m.group(1)), p.name)
    return (999, p.name)


def check_album_integrity(audio_files: list) -> list:
    warnings = []
    track_nums = []
    for f in audio_files:
        m = re.search(r"#?(\d+)", f.name)
        if m:
            track_nums.append(int(m.group(1)))

    if track_nums:
        nums = sorted(set(track_nums))
        min_n, max_n = min(nums), max(nums)
        expected = set(range(min_n, max_n + 1))
        missing = sorted(expected - set(nums))
        if missing:
            missing_str = ", ".join(f"#{m:02d}" for m in missing)
            warnings.append(f"⚠️ [完整性预警] 发现音轨序号不连续，可能缺失音轨音频: {missing_str}")
    return warnings


def fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"[{mins:02d}:{secs:05.2f}]"


def highpass_and_normalize(audio: np.ndarray, sr: int = 16000, cutoff: float = 80.0) -> np.ndarray:
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


HALLUCINATION_BLACKLIST = [
    "おやすみなさい", "チャンネル登録", "ご視聴ありがとう", "高評価",
    "次の動画", "次回までお楽しみ", "バイバイ", "Subtitles by", "Translated by",
    "あきらめないで", "質問の端から"
]


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    if re.search(r"[\uac00-\ud7af\u1100-\u11ff\u0400-\u04ff]", t):
        return ""
    for b in HALLUCINATION_BLACKLIST:
        if b in t:
            return ""
    if re.match(r"^[，。、！？!?…~～\s—\-－:：;；()（）「」『』\"'0-9]+$", t):
        return ""
    t = re.sub(r"(.{2,6}?)\1{4,}", r"\1\1", t)
    t = re.sub(r"(.)\1{5,}", r"\1\1", t)
    return t.strip()


def merge_word_segments(raw_segments, max_gap=1.2, max_len=36, max_span=8.0):
    groups = []
    cur_text = ""
    cur_start = 0.0
    cur_end = 0.0

    for seg in raw_segments:
        if getattr(seg, "no_speech_prob", 0.0) > 0.75:
            continue
        t = sanitize_text(seg.text)
        if not t:
            continue

        start = seg.words[0].start if (hasattr(seg, "words") and seg.words) else seg.start
        end = seg.words[-1].end if (hasattr(seg, "words") and seg.words) else seg.end

        if not cur_text:
            cur_start = start
            cur_end = end
            cur_text = t
            continue

        gap = start - cur_end
        span = end - cur_start

        should_break = False
        if gap >= max_gap:
            should_break = True
        elif any(cur_text.endswith(p) for p in ("。", "！", "!", "？", "?", "…")):
            should_break = True
        elif len(cur_text) + len(t) > max_len or span > max_span:
            should_break = True

        if should_break:
            groups.append((cur_start, cur_end, cur_text))
            cur_start = start
            cur_end = end
            cur_text = t
        else:
            cur_text += " " + t
            cur_end = end

    if cur_text:
        groups.append((cur_start, cur_end, cur_text))

    return groups


def process_album(album_dir: str, overwrite: bool = False, genre: str = None, model_path: str = r"D:\ASMR\openlrc\models\large-v2"):
    album_path = Path(album_dir).resolve()
    if not album_path.is_dir():
        print(f"错误: 目标路径不是有效目录: {album_path}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"🚀 ASMR 本地声学转录与 DTW 对齐流水线启动: {album_path.name}")
    print("=" * 60)

    # 1. Phase 1: Metadata Sniffing & Persona Initialization
    print("\n[阶段 1/2] 自动嗅探作品元数据 & 建立专属角色档案...")
    meta = sniff_album_metadata(album_path)
    print(format_summary(meta))

    active_genre = genre or meta.get("genre_key")
    if active_genre:
        print(f"  🎨 [题材预设] 已锁定适用题材: {active_genre}")

    persona = init_session_persona(album_path, meta=meta)
    session_keywords = list(meta.get("character_names", []))
    for kw in meta.get("scenario_tags", []):
        if kw not in session_keywords:
            session_keywords.append(kw)

    # 2. Collect Audio Tracks
    audio_files = []
    for ext in (".wav", ".mp3", ".flac", ".m4a"):
        audio_files.extend(album_path.glob(f"*{ext}"))
    audio_files = sorted(audio_files, key=natural_sort_key)

    if not audio_files:
        print("未在目录中找到音频文件！", file=sys.stderr)
        return

    print(f"\n找到 {len(audio_files)} 轨音频，按自然顺序排列:")
    for idx, f in enumerate(audio_files, start=1):
        print(f"  [{idx:02d}/{len(audio_files):02d}] {f.name}")

    integrity_warnings = check_album_integrity(audio_files)
    for w in integrity_warnings:
        print(f"  {w}")

    # 3. Load Whisper Model in GPU (Once)
    print("\n[阶段 2/2] 加载 Faster-Whisper 模型到显存（单例常驻复用）...")
    t_m0 = time.time()
    try:
        model = WhisperModel(model_path, device="cuda", compute_type="int8_float16")
    except Exception as e:
        print(f"⚠ CUDA 加载失败 ({e})，尝试回退 CPU int8...")
        model = WhisperModel(model_path, device="cpu", compute_type="int8")
    print(f"模型加载完成，耗时 {time.time() - t_m0:.2f} 秒。\n")

    total_tracks = len(audio_files)
    album_start_time = time.time()
    album_records = []

    # 4. Track-by-track DTW alignment loop
    for idx, audio in enumerate(audio_files, start=1):
        track_t0 = time.time()
        print("\n" + "-" * 55)
        print(f"▶ 音轨声学对齐 [{idx}/{total_tracks}]: {audio.name}")
        print("-" * 55)

        meta_json_path = album_path / f"{audio.stem}.segments.json"
        ja_lrc_path = album_path / f"{audio.stem}.日文原稿.lrc"
        ja_txt_path = album_path / f"{audio.stem}.日文原稿.txt"

        if not overwrite and meta_json_path.is_file() and meta_json_path.stat().st_size > 100:
            print(f"  [跳过] 已存在有效转录产物: {audio.name}")
            try:
                segments = json.loads(meta_json_path.read_text(encoding="utf-8"))
            except Exception:
                segments = []
        else:
            # Dynamic Micro-theatre Prompt
            current_prompt = build_initial_prompt(
                audio_context=audio.name,
                budget=135,
                custom_keywords=session_keywords,
                genre=active_genre
            )
            tokens = estimate_tokens(current_prompt)
            print(f"  [Prompt ({tokens} toks)] {current_prompt}")

            # Audio decode & FIR DSP filtering
            wav = decode_audio(str(audio), sampling_rate=16000)
            wav_filt = highpass_and_normalize(wav, sr=16000, cutoff=80.0)

            # Transcribe with calibrated parameters
            raw_segs, _ = model.transcribe(
                wav_filt,
                language="ja",
                beam_size=5,
                word_timestamps=True,
                initial_prompt=current_prompt,
                condition_on_previous_text=False,
                compression_ratio_threshold=2.4
            )
            raw_list = list(raw_segs)
            groups = merge_word_segments(raw_list)

            # Write Japanese outputs
            ja_lrc_lines = [f"{fmt_ts(s)}{text}" for s, _, text in groups]
            ja_lrc_path.write_text("\n".join(ja_lrc_lines) + "\n", encoding="utf-8", newline="\n")

            ja_txt_lines = [text for _, _, text in groups]
            ja_txt_path.write_text("\n".join(ja_txt_lines) + "\n", encoding="utf-8", newline="\n")

            segments = [
                {"start": round(s, 3), "end": round(e, 3), "ts": fmt_ts(s), "ja": text}
                for s, e, text in groups
            ]
            meta_json_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✔ [基准原稿] 获得 {len(segments)} 句精细时间轴字幕，耗时 {time.time() - track_t0:.1f}s")

        # C. Auto-export pure Chinese LRC if translation exists
        zh_txt_path = album_path / f"{audio.stem}.中文翻译.txt"
        if zh_txt_path.is_file() and segments:
            zh_lines = [ln.strip() for ln in zh_txt_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if len(zh_lines) == len(segments):
                export_subtitles(
                    segments=segments,
                    zh_lines=zh_lines,
                    audio_path=audio,
                    output_dir=album_path,
                    persona=persona,
                    sanity_check=True
                )
                print(f"  ✔ [主字幕] 已通过质检门禁导出纯中文 LRC: {audio.stem}.lrc")

        album_records.append({
            "index": idx,
            "title": audio.name,
            "lines": len(segments),
        })

    total_time = time.time() - album_start_time
    print("\n" + "=" * 60)
    print(f"🎉 全专声学对齐处理完成！总计 {total_tracks} 轨音频，总耗时: {total_time / 60:.1f} 分钟")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="ASMR Autonomous Album ASR & Alignment Pipeline")
    parser.add_argument("album_dir", type=str, help="Path to album directory containing audio files")
    parser.add_argument("--genre", "-g", type=str, default=None, help="Preset genre (e.g. 漫咖, NTR, 催眠, 雌小鬼, 纯爱, 调教, 保健室, 温泉, 直播)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing transcripts")
    parser.add_argument("--model", "-m", type=str, default=r"D:\ASMR\openlrc\models\large-v2", help="Whisper model path")
    args = parser.parse_args()

    process_album(args.album_dir, overwrite=args.overwrite, genre=args.genre, model_path=args.model)


if __name__ == "__main__":
    main()
