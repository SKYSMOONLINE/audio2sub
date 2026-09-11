# -*- coding: utf-8 -*-
"""
ASMR Metadata Sniffer
- Scans album directory and its parent for readme.txt, info.txt, HTML, NFO files.
- Recursively inspects sibling directories (e.g. EXデータ, 特典) under the RJ folder.
- Extracts RJ code, Title, Circle/社团, CV/声优, Character names, and Scenario tags.
- Feeds structured metadata into prompt_builder to boost Whisper recognition accuracy.
"""

import os
import sys
import re
import json
import argparse
from pathlib import Path

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RJ_PATTERN = re.compile(r"(RJ\d{6,8}|VJ\d{6,8}|BJ\d{6,8})", re.IGNORECASE)

SCENARIO_KEYWORDS = [
    # 场景与场所
    "漫画喫茶", "漫喫", "ネカフェ", "ネットカフェ", "個室", "同棲", "ホテル", "保健室", "教室", "放課後",
    # 玩法与设定
    "キメセク", "媚薬", "催眠", "アナル", "前立腺", "中出し", "生中出し", "浮気", "NTR", "寝取られ",
    "パパ活", "援交", "痴女", "耳かき", "添い寝", "マッサージ", "エステ", "ASMR", "フォーリー",
    "配信", "生配信", "vtuber", "垢バン", "おじさん", "オジサン", "叔父", "ペア", "保体",
    # 人设
    "幼馴染", "後輩", "先輩", "妹", "姉", "母", "義母", "女上司", "同僚", "転校生", "先生", "同級生",
    "メスガキ", "ざぁこ", "ツンデレ", "クーデレ", "ヤンデレ", "甘々", "純情"
]


def _read_file_safe(path: Path) -> str:
    """Reads a file with utf-8, fallback to cp932 (Shift-JIS) or gbk."""
    for enc in ("utf-8", "cp932", "shift_jis", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    return ""


def sniff_album_metadata(target_dir: Path) -> dict:
    target_dir = Path(target_dir).resolve()
    if not target_dir.is_dir():
        return {}

    # Determine root album folder (support JP/CN naming and common audio subdirectories)
    album_root = target_dir
    subfolder_aliases = (
        "本編", "正篇", "音声", "特典", "本篇音声", "广播剧", "音频", "track", "tracks",
        "wav", "mp3", "flac", "audio", "main", "disc1", "disc2", "cd1", "cd2"
    )
    if target_dir.name.lower() in subfolder_aliases and target_dir.parent.is_dir():
        album_root = target_dir.parent

    # 1. Detect RJ code from paths (inspect target, album_root, and parent folders)
    rj_code = None
    all_path_parts = [target_dir.name, album_root.name]
    if album_root.parent.is_dir():
        all_path_parts.append(album_root.parent.name)
    all_path_str = " ".join(all_path_parts)
    rj_match = RJ_PATTERN.search(all_path_str)
    if rj_match:
        rj_code = rj_match.group(1).upper()

    # 2. Collect text/info files up to 2 subfolder levels deep in album_root
    doc_files = []
    text_corpus = []

    for p in album_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".txt", ".nfo", ".html", ".htm", ".json", ".md"):
            # Skip generated subtitle or script files
            if any(x in p.name for x in [".lrc", ".segments.", ".日文原稿", ".中文翻译", "prompt", "fix"]):
                continue
            doc_files.append(p)
            content = _read_file_safe(p)
            if content:
                rel_path = str(p.relative_to(album_root))
                text_corpus.append((rel_path, content))

    full_text = "\n".join([f"=== {fn} ===\n{c}" for fn, c in text_corpus])
    folder_context = f"{album_root.name} {target_dir.name}"
    combined_context = f"{folder_context}\n{full_text}"

    # 3. Detect Title
    title = None
    title_matches = re.findall(r"[『「]([^』」\n]{4,40})[』」]", full_text)
    for tm in title_matches:
        if not any(x in tm for x in ["利用規約", "はじめに", "インストール", "免責事項", "ダウンロード"]):
            title = tm.strip()
            break
    if not title:
        candidate_names = [album_root.name]
        if (album_root.name.lower() in subfolder_aliases or len(album_root.name) < 4 or (rj_code and album_root.name.upper() == rj_code)) and album_root.parent.is_dir():
            candidate_names.insert(0, album_root.parent.name)

        for c_name in candidate_names:
            clean_name = re.sub(r"\[[^\]]+\]|\([^\)]+\)", "", c_name).strip()
            clean_name = RJ_PATTERN.sub("", clean_name).strip(" _-")
            if clean_name and len(clean_name) >= 3 and clean_name.lower() not in subfolder_aliases:
                title = clean_name
                break
        if not title:
            title = album_root.name

    # 4. Detect CV / 出演者 & Character roles
    cv_list = []
    character_names = []

    # Case A: 出演：xxx \n yyy（角色役）
    cast_blocks = re.findall(r"(?:出演|キャスト|CV|声優|ボイス)[：:\s]+([^\n\r]+(?:\n[ \t　]+[^\n\r]+)*)", full_text, re.IGNORECASE)
    for block in cast_blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        for line in lines:
            # Check role in parentheses e.g. 柚木つばめ（先生役）
            role_m = re.search(r"（([^）]+役)）|\(([^)]+役)\)", line)
            if role_m:
                role = (role_m.group(1) or role_m.group(2)).replace("役", "").strip()
                if role and role not in character_names:
                    character_names.append(role)
            # Strip parentheses to get CV
            cv_clean = re.sub(r"（[^）]+）|\([^)]+\)|[【】\[\]「」]", "", line).strip()
            # Split multiple CVs by commas or slashes
            for cv_part in re.split(r"[,，/／、\s]+", cv_clean):
                cv_part = cv_part.strip()
                if cv_part and 1 < len(cv_part) <= 15 and cv_part not in cv_list:
                    if cv_part not in ("あり", "なし", "未定", "様", "さん", "他"):
                        cv_list.append(cv_part)

    # Case B: Tag patterns in filenames or brackets [CV: xxx]
    cv_bracket_matches = re.findall(r"(?:\[|\(|【)(?:CV|声優|ボイス)[：:\s]*([^\]\)】]+)(?:\]|\)|】)", folder_context, re.IGNORECASE)
    for m in cv_bracket_matches:
        for cv_part in re.split(r"[,，/／、\s]+", m.strip()):
            cv_part = cv_part.strip()
            if cv_part and 1 < len(cv_part) <= 15 and cv_part not in cv_list:
                cv_list.append(cv_part)

    # 5. Detect Circle / 社团
    circle_name = None
    circle_patterns = [
        re.compile(r"(?:【\s*サークル(?:\s*名)?\s*】|\[\s*サークル(?:\s*名)?\s*\])[：:\s]*([^\r\n]+)"),
        re.compile(r"(?:当サークル作品|サークル(?:\s*名)?|Circle|ブランド)[：:\s]+([^\r\n『「]+)", re.IGNORECASE),
    ]
    for pat in circle_patterns:
        m = pat.search(full_text)
        if m:
            c = m.group(1).strip()
            c = re.sub(r"[【】\[\]]", "", c).strip()
            if c and len(c) <= 30 and not c.startswith("『"):
                circle_name = c
                break

    if not circle_name:
        b_match = re.search(r"\[([^\]]+)\]", album_root.name)
        if b_match and not RJ_PATTERN.match(b_match.group(1)):
            circle_name = b_match.group(1).strip()

    # 6. Detect Character Names from story
    # Look for patterns like "鈴木さん", "アリスちゃん"
    name_matches = re.findall(r"([一-龠]{1,4}(?:さん|ちゃん|先生|先輩|後輩)|[ァ-ヶー]{2,6}(?:さん|ちゃん))", full_text)
    for nm in name_matches:
        nm = re.sub(r"^(?:たい|ない|ます|です|して|から|ので|けど|と|に|へ|で|を|は|が)", "", nm).strip()
        if nm not in ("お母さん", "お父さん", "お姉さん", "お兄さん", "皆さん", "たくさん", "お客さん", "保体の先生"):
            if nm and nm not in character_names and 2 <= len(nm) <= 8:
                character_names.append(nm)

    # 7. Detect Scenario Keywords
    detected_scenarios = []
    for kw in SCENARIO_KEYWORDS:
        if kw in combined_context and kw not in detected_scenarios:
            detected_scenarios.append(kw)

    # 8. Infer Genre Preset
    detected_genre = None
    detected_genre_key = None
    try:
        from prompt_builder import resolve_genre
        g_key, g_info = resolve_genre(audio_context=f"{title} {combined_context} {' '.join(detected_scenarios)}")
        if g_info:
            detected_genre = f"{g_info['name']} ({g_key})"
            detected_genre_key = g_key
    except Exception:
        pass

    return {
        "album_root": str(album_root),
        "target_dir": str(target_dir),
        "rj_code": rj_code,
        "title": title,
        "circle": circle_name,
        "cv_list": cv_list,
        "character_names": character_names,
        "scenario_tags": detected_scenarios,
        "detected_genre": detected_genre,
        "genre_key": detected_genre_key,
        "detected_doc_files": [f.name for f in doc_files],
    }


def format_summary(meta: dict) -> str:
    lines = [
        "==================================================",
        "🎧 音声作品元数据嗅探报告",
        "==================================================",
        f"  作品根目录: {meta.get('album_root', '')}",
    ]
    if meta.get("rj_code"):
        lines.append(f"  RJ 编号:   {meta['rj_code']}")
    if meta.get("title"):
        lines.append(f"  作品标题:   {meta['title']}")
    if meta.get("circle"):
        lines.append(f"  制作社团:   {meta['circle']}")
    if meta.get("detected_genre"):
        lines.append(f"  推荐题材:   {meta['detected_genre']}")
    if meta.get("cv_list"):
        lines.append(f"  声优 (CV):  {', '.join(meta['cv_list'])}")
    else:
        lines.append(f"  声优 (CV):  [未在文档中检出，将由首轨实时推断]")
    if meta.get("character_names"):
        lines.append(f"  登场角色:   {', '.join(meta['character_names'][:5])}")
    if meta.get("scenario_tags"):
        lines.append(f"  题材标签:   {', '.join(meta['scenario_tags'][:10])}")
    if meta.get("detected_doc_files"):
        lines.append(f"  参考文档:   {', '.join(meta['detected_doc_files'][:6])}")
    lines.append("==================================================")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ASMR Album Metadata Sniffer")
    parser.add_argument("dir", type=str, help="Path to album directory or track directory")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    meta = sniff_album_metadata(Path(args.dir))
    if args.json:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    else:
        print(format_summary(meta))


if __name__ == "__main__":
    main()
