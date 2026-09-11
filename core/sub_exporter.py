# -*- coding: utf-8 -*-
"""
ASMR Subtitle Exporter
- Exports standard subtitle files from aligned segments and 1:1 translated lines.
- Default output: Pure Chinese LRC ([mm:ss.xx]中文) for a clean, player-friendly interface.
- Also writes reference files:
    - [Track].日文原稿.lrc
    - [Track].日文原稿.txt
    - [Track].中文翻译.txt
    - [Track].segments.json
- Optional formats:
    - Inline bilingual LRC: [Track].双语.lrc ([mm:ss.xx]中文（日文）)
    - Standard SRT: [Track].srt
"""

import os
import sys
import re
import json
import argparse
from pathlib import Path
from difflib import SequenceMatcher

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from session_persona import normalize_listener_address, load_session_persona
except ImportError:
    try:
        from scripts.session_persona import normalize_listener_address, load_session_persona
    except ImportError:
        def normalize_listener_address(text, persona=None):
            m = re.findall(r"(老爷爷|老爷子|小老头|老头子|爷爷|老汉)", text)
            return re.sub(r"(老爷爷|老爷子|小老头|老头子|爷爷|老汉)", "大叔", text), len(m)
        def load_session_persona(album_dir):
            return {}


def fmt_lrc_ts(seconds: float) -> str:
    """Format seconds into standard [MM:SS.xx] LRC timestamp."""
    seconds = max(0.0, float(seconds))
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"[{mins:02d}:{secs:05.2f}]"


def fmt_srt_ts(seconds: float) -> str:
    """Format seconds into standard 00:00:00,000 SRT timestamp."""
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    hrs, rem = divmod(total_ms, 3600000)
    mins, rem = divmod(rem, 60000)
    secs, ms = divmod(rem, 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


PUNCTUATION_ONLY_REGEX = re.compile(r"^[，。、！？!?…~～\s—\-－:：;；()（）「」『』\"'0-9]+$")

PROMPT_LEAK_PHRASES = [
    "音声作品の台詞", "耳元で優しく", "囁きかけています", "これは音声作品",
    "これは音声作品の台詞です", "語彙：", "音声作品",
    "这是一段音声作品", "在耳边温柔地耳语", "词汇："
]


def apply_sanity_gate(
    segments: list,
    zh_lines: list,
    prompt: str = "",
    persona: dict = None,
    time_threshold: float = 6.0
) -> tuple:
    """
    4-Tier Automated Subtitle Sanity Gate:
    1. Line Symmetry: Verifies len(segments) == len(zh_lines).
    2. Prompt Echo Scrubber: Removes lines in opening silence that leak prompt framing.
    3. Listener Address Normalization: Auto-heals "爷爷/老爷子/小老头" -> "大叔".
    4. Punctuation & Empty Line Elimination: Drops noise/empty lines symmetrically.
    Returns: (clean_segments, clean_zh_lines, health_report)
    """
    orig_len = len(segments)
    if orig_len != len(zh_lines):
        raise ValueError(
            f"Segment count ({orig_len}) does not match Chinese line count ({len(zh_lines)})!"
        )

    clean_segments = []
    clean_zh_lines = []
    echoes_dropped = 0
    addresses_healed = 0
    empty_dropped = 0

    prompt_words = set(re.findall(r"[\u4e00-\u9fa5\u3040-\u309f\u30a0-\u30ff]{2,}", prompt)) if prompt else set()

    for idx, (seg, zh) in enumerate(zip(segments, zh_lines)):
        s = float(seg.get("start", 0.0))
        ja = str(seg.get("ja", "")).strip()
        zh_str = str(zh).strip()

        # Tier 2: Opening Silence Prompt Echo Check
        if s < time_threshold:
            is_leak = any(phrase in ja or phrase in zh_str for phrase in PROMPT_LEAK_PHRASES)
            if not is_leak and prompt_words and len(ja) >= 4:
                ja_words = set(re.findall(r"[\u4e00-\u9fa5\u3040-\u309f\u30a0-\u30ff]{2,}", ja))
                if ja_words:
                    overlap = len(ja_words & prompt_words) / len(ja_words)
                    if overlap >= 0.7:
                        is_leak = True
            if is_leak:
                echoes_dropped += 1
                continue

        # Tier 3: Out-of-Character Listener Address Normalization
        zh_healed, healed_count = normalize_listener_address(zh_str, persona=persona)
        addresses_healed += healed_count

        # Tier 4: Pure Punctuation & Empty Line Check
        if not zh_healed or PUNCTUATION_ONLY_REGEX.match(zh_healed):
            empty_dropped += 1
            continue
        if not ja or PUNCTUATION_ONLY_REGEX.match(ja):
            empty_dropped += 1
            continue

        clean_segments.append(seg)
        clean_zh_lines.append(zh_healed)

    report = {
        "original_count": orig_len,
        "clean_count": len(clean_segments),
        "echoes_dropped": echoes_dropped,
        "addresses_healed": addresses_healed,
        "empty_dropped": empty_dropped,
        "valid": len(clean_segments) > 0
    }

    return clean_segments, clean_zh_lines, report


def export_subtitles(
    segments: list,
    zh_lines: list,
    audio_path: Path,
    output_dir: Path = None,
    export_bilingual: bool = False,
    export_srt: bool = False,
    prompt: str = "",
    persona: dict = None,
    sanity_check: bool = True
) -> dict:
    """
    Exports subtitle bundle for an audio track.
    segments: list of dicts [{"start": float, "end": float, "ja": str}, ...]
    zh_lines: list of strings (1:1 with segments)
    audio_path: path to the audio file
    """
    audio_path = Path(audio_path)
    out_dir = Path(output_dir) if output_dir else audio_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem

    if sanity_check:
        segments, zh_lines, health = apply_sanity_gate(
            segments, zh_lines, prompt=prompt, persona=persona
        )
        print(
            f"  ✔ [质检门禁] {health['clean_count']}/{health['original_count']} 句对齐 | "
            f"自愈修正: {health['addresses_healed']}处称呼 | "
            f"剔除回响: {health['echoes_dropped']}句 | "
            f"剔除空行标点: {health['empty_dropped']}句"
        )
    elif len(segments) != len(zh_lines):
        raise ValueError(
            f"Segment count ({len(segments)}) does not match Chinese line count ({len(zh_lines)})!"
        )

    # Prepare lines
    merged_data = []
    zh_lrc_lines = []
    ja_lrc_lines = []
    bi_lrc_lines = []
    srt_blocks = []

    for idx, (seg, zh) in enumerate(zip(segments, zh_lines), start=1):
        s = float(seg.get("start", 0.0))
        e = float(seg.get("end", s + 2.0))
        ja = str(seg.get("ja", "")).strip()
        zh = str(zh).strip()

        ts = fmt_lrc_ts(s)
        merged_data.append({
            "index": idx,
            "start": round(s, 3),
            "end": round(e, 3),
            "ts": ts,
            "ja": ja,
            "zh": zh
        })

        # 1. Pure Chinese LRC
        zh_lrc_lines.append(f"{ts}{zh}")

        # 2. Pure Japanese LRC
        ja_lrc_lines.append(f"{ts}{ja}")

        # 3. Bilingual inline LRC
        bi_text = f"{zh}（{ja}）" if (zh and ja) else (zh or ja)
        bi_lrc_lines.append(f"{ts}{bi_text}")

        # 4. SRT block
        if export_srt:
            srt_start = fmt_srt_ts(s)
            srt_end = fmt_srt_ts(e)
            srt_text = f"{zh}\n{ja}" if (zh and ja) else (zh or ja)
            srt_blocks.append(f"{idx}\n{srt_start} --> {srt_end}\n{srt_text}\n")

    # File paths
    main_lrc_path = out_dir / f"{stem}.lrc"
    ja_lrc_path = out_dir / f"{stem}.日文原稿.lrc"
    ja_txt_path = out_dir / f"{stem}.日文原稿.txt"
    zh_txt_path = out_dir / f"{stem}.中文翻译.txt"
    json_path = out_dir / f"{stem}.segments.json"

    # Write files (UTF-8, LF)
    main_lrc_path.write_text("\n".join(zh_lrc_lines) + "\n", encoding="utf-8", newline="\n")
    ja_lrc_path.write_text("\n".join(ja_lrc_lines) + "\n", encoding="utf-8", newline="\n")
    ja_txt_path.write_text("\n".join(seg.get("ja", "") for seg in segments) + "\n", encoding="utf-8", newline="\n")
    zh_txt_path.write_text("\n".join(zh_lines) + "\n", encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(merged_data, ensure_ascii=False, indent=2), encoding="utf-8")

    generated = {
        "main_lrc": str(main_lrc_path),
        "ja_lrc": str(ja_lrc_path),
        "ja_txt": str(ja_txt_path),
        "zh_txt": str(zh_txt_path),
        "segments_json": str(json_path),
    }

    if export_bilingual:
        bi_lrc_path = out_dir / f"{stem}.双语.lrc"
        bi_lrc_path.write_text("\n".join(bi_lrc_lines) + "\n", encoding="utf-8", newline="\n")
        generated["bilingual_lrc"] = str(bi_lrc_path)

    if export_srt:
        srt_path = out_dir / f"{stem}.srt"
        srt_path.write_text("\n".join(srt_blocks) + "\n", encoding="utf-8", newline="\n")
        generated["srt"] = str(srt_path)

    return generated


def main():
    parser = argparse.ArgumentParser(description="ASMR Subtitle Exporter")
    parser.add_argument("--json", "-j", type=str, required=True, help="Path to .segments.json")
    parser.add_argument("--zh", "-z", type=str, default=None, help="Path to .中文翻译.txt (1:1 aligned)")
    parser.add_argument("--zh-json", type=str, default=None, help="Path to JSON file containing array of Chinese lines (immune to shell quote issues)")
    parser.add_argument("--audio", "-a", type=str, default=None, help="Base audio file path (for naming)")
    parser.add_argument("--out-dir", "-o", type=str, default=None, help="Output directory")
    parser.add_argument("--bilingual", action="store_true", help="Also export [Track].双语.lrc")
    parser.add_argument("--srt", action="store_true", help="Also export [Track].srt")
    args = parser.parse_args()

    json_p = Path(args.json)

    if not json_p.is_file():
        print(f"错误: 找不到 segments JSON 文件: {json_p}", file=sys.stderr)
        sys.exit(1)

    segments = json.loads(json_p.read_text(encoding="utf-8"))

    zh_lines = []
    if args.zh_json:
        zj_p = Path(args.zh_json)
        if not zj_p.is_file():
            print(f"错误: 找不到中文翻译 JSON 文件: {zj_p}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(zj_p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            zh_lines = [str(item).strip() for item in data if str(item).strip()]
        # Also persist to .中文翻译.txt
        if args.zh:
            Path(args.zh).write_text("\n".join(zh_lines) + "\n", encoding="utf-8")
        else:
            txt_p = json_p.parent / json_p.name.replace(".segments.json", ".中文翻译.txt")
            txt_p.write_text("\n".join(zh_lines) + "\n", encoding="utf-8")
    elif args.zh:
        zh_p = Path(args.zh)
        if not zh_p.is_file():
            print(f"错误: 找不到中文翻译文件: {zh_p}", file=sys.stderr)
            sys.exit(1)
        # Safe read with fallback
        for enc in ("utf-8", "utf-8-sig", "gbk", "cp936"):
            try:
                zh_lines = [ln.strip() for ln in zh_p.read_text(encoding=enc).splitlines() if ln.strip()]
                break
            except UnicodeDecodeError:
                continue
    else:
        print("错误: 必须指定 --zh 或 --zh-json", file=sys.stderr)
        sys.exit(1)

    audio_base = Path(args.audio) if args.audio else json_p.with_name(json_p.name.replace(".segments.json", ".wav"))
    persona = load_session_persona(audio_base.parent)
    res = export_subtitles(
        segments=segments,
        zh_lines=zh_lines,
        audio_path=audio_base,
        output_dir=Path(args.out_dir) if args.out_dir else None,
        export_bilingual=args.bilingual,
        export_srt=args.srt,
        persona=persona
    )

    print("✔ 字幕文件导出成功:")
    for k, v in res.items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
