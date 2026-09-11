"""Subtitle rendering and common text formats."""
from __future__ import annotations
import json
from pathlib import Path
from .postprocess import clean_fillers_cn

def fmt_ts(t: float) -> str:
    t = max(0, int(round(t * 100))); mm, rem = divmod(t, 6000); ss, cc = divmod(rem, 100)
    return f"[{mm:02d}:{ss:02d}.{cc:02d}]"

def _row(item):
    if hasattr(item, "start"): return item.start, item.end, item.ja, item.zh, item.speaker
    if isinstance(item, dict): return item["start"], item["end"], item.get("ja", ""), item.get("zh", ""), item.get("speaker")
    return (*item, None) if len(item) == 4 else tuple(item)

def build_outputs(groups_ja_zh, mode="zh_clean"):
    _validate_mode(mode)
    lines = []
    for item in groups_ja_zh:
        start, _end, text = _text(item, mode)
        if text: lines.append(fmt_ts(start) + text)
    return lines

def _vtt_ts(value: float) -> str:
    ms = max(0, int(round(value * 1000))); h, rem = divmod(ms, 3600000); m, rem = divmod(rem, 60000); s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def _validate_mode(mode):
    if mode not in {"ja", "zh", "zh_clean", "bilingual"}:
        raise ValueError(f"unsupported subtitle mode: {mode}")

def _text(item, mode="zh"):
    _validate_mode(mode)
    start, end, ja, zh, speaker = _row(item)
    ja, zh = (ja or "").strip(), (zh or "").strip()
    text = ja if mode == "ja" else zh or ja
    if mode == "zh_clean": text = clean_fillers_cn(zh) if zh else ""
    if mode == "bilingual" and zh: text = f"{zh}（{ja}）"
    return start, end, (f"{speaker}：" if speaker and text else "") + text

def _srt_ts(value):
    return _vtt_ts(value).replace(".", ",")

def render_srt(items, mode="zh"):
    _validate_mode(mode)
    rows = (row for row in (_text(item, mode) for item in items) if row[2])
    body = "\n\n".join(f"{i}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{text}" for i, (s, e, text) in enumerate(rows, 1))
    return body + ("\n" if body else "")

def render_vtt(items, mode="zh"):
    _validate_mode(mode)
    body = "\n\n".join(f"{_vtt_ts(s)} --> {_vtt_ts(e)}\n{text}" for item in items for s, e, text in [_text(item, mode)] if text)
    return "WEBVTT\n\n" + body + ("\n" if body else "")

def save_subtitles(items, output_dir, stem="subtitles", mode="zh"):
    _validate_mode(mode)
    items = list(items)
    directory = Path(output_dir); directory.mkdir(parents=True, exist_ok=True)
    segments = [item.to_dict() if hasattr(item, "to_dict") else item for item in items]
    lrc = build_outputs(items, mode)
    paths = {"json": directory / f"{stem}.json", "lrc": directory / f"{stem}.lrc", "srt": directory / f"{stem}.srt", "vtt": directory / f"{stem}.vtt"}
    paths["json"].write_text(json.dumps(segments, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["lrc"].write_text("\n".join(lrc) + ("\n" if lrc else ""), encoding="utf-8")
    paths["srt"].write_text(render_srt(items, mode), encoding="utf-8")
    paths["vtt"].write_text(render_vtt(items, mode), encoding="utf-8")
    return paths

def save_lrc(lines, output_path) -> Path:
    output_path = Path(output_path); output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n"); return output_path

def output_name(audio_path, suffix="", out_dir=None):
    folder = Path(out_dir) if out_dir else Path(audio_path).parent; folder.mkdir(parents=True, exist_ok=True)
    base = f"{Path(audio_path).stem}{('.' + suffix) if suffix else ''}"; path = folder / f"{base}.lrc"; number = 1
    while path.exists(): path = folder / f"{base}_{number}.lrc"; number += 1
    return path
