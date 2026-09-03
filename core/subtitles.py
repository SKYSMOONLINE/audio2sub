# -*- coding: utf-8 -*-
"""
字幕生成模块：时间轴 + 文本 -> LRC 文件
支持三种内容形态：
  - 双语:   [时间]中文译文（日文原文）
  - 中文:   [时间]中文译文
  - 中文精简: 中文版基础上做语气词删减
"""
from pathlib import Path

from .postprocess import clean_fillers_cn


def fmt_ts(t: float) -> str:
    """秒 -> LRC 时间戳 [mm:ss.xx]（百分秒，取整）"""
    t = max(0, int(round(t * 100)))
    mm, rem = divmod(t, 6000)
    ss, cc = divmod(rem, 100)
    return f"[{mm:02d}:{ss:02d}.{cc:02d}]"


def build_outputs(groups_ja_zh, mode="zh_clean"):
    """
    根据模式把 [(start, end, ja, zh), ...] 转成字幕行列表。
    mode:
      'bilingual'  -> [时间]中文（日文）
      'zh'         -> [时间]中文
      'zh_clean'   -> [时间]中文（已删语气词）
    """
    lines = []
    for start, end, ja, zh in groups_ja_zh:
        ts = fmt_ts(start)
        if mode == "bilingual":
            text = f"{zh}（{ja}）" if zh else ja
        elif mode == "zh":
            text = zh if zh else ja
        else:  # zh_clean
            zh_c = clean_fillers_cn(zh) if zh else ""
            text = zh_c if zh_c else None
        if text:
            lines.append(ts + text)
    return lines


def save_lrc(lines, output_path) -> Path:
    """写入 LRC 文件（UTF-8，无 BOM，LF 换行）"""
    output_path = Path(output_path)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return output_path


def output_name(audio_path, suffix="", out_dir=None):
    """Return a collision-free LRC path using the GUI naming convention."""
    folder = Path(out_dir) if out_dir else Path(audio_path).parent
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(audio_path).stem
    base = f"{stem}{('.' + suffix) if suffix else ''}"
    path = folder / f"{base}.lrc"
    number = 1
    while path.exists():
        path = folder / f"{base}_{number}.lrc"
        number += 1
    return path
