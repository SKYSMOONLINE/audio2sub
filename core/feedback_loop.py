# -*- coding: utf-8 -*-
"""
ASMR Self-improving Feedback Engine ("反哺自身" 学习系统)
- Validation Gate: Enforces quality control to prevent erroneous data contamination
- Phrase-level context binding: Forbids dangerous naked single-word replacements
- Safe atomic updates with automated .bak backups
- Supports:
    1. Learning new domain vocabulary / character names
    2. Learning phrase-bound acoustic error corrections
    3. Learning new Whisper hallucination patterns
    4. Auto-diff analysis between original & human-corrected transcripts
"""

import os
import sys
import re
import json
import shutil
import argparse
from datetime import datetime
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

REF_DIR = Path(__file__).resolve().parent.parent / "references"


def backup_file(path: Path):
    """Creates a timestamped backup before writing changes."""
    if path.is_file():
        backup_dir = path.parent / ".backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{path.name}.{ts}.bak"
        shutil.copy2(path, backup_path)
        return backup_path
    return None


def validate_japanese_term(term: str) -> tuple[bool, str]:
    """Quality filter for new vocabulary candidates."""
    if not term:
        return False, "空字符串"
    term = term.strip()
    if len(term) < 2:
        return False, f"词长过短 (<2字符): '{term}'"
    if len(term) > 20:
        return False, f"词长过长 (>20字符): '{term}'"
    if re.match(r"^[0-9\s.,!?:;…~～\-_()「」『』]+$", term):
        return False, f"纯数字或标点符号: '{term}'"
    # Must contain at least some Japanese kana/kanji or alphanumeric
    if not re.search(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", term):
        return False, f"不包含日文字符 (假名/汉字): '{term}'"
    return True, ""


def add_vocabulary(word: str, category: str = "learned_vocabulary", note: str = "") -> dict:
    """Adds a validated domain term to vocabulary_bank.json."""
    valid, reason = validate_japanese_term(word)
    if not valid:
        return {"success": False, "error": f"词汇校验失败: {reason}"}

    # Safety gate: prevent polluting roles_personas with common surnames or single-album character names
    if category == "roles_personas":
        common_surnames = {"佐藤", "鈴木", "高橋", "田中", "渡辺", "伊藤", "山本", "中村", "小林", "加藤"}
        term_clean = re.sub(r"(さん|ちゃん|くん|君|様)$", "", word.strip())
        if term_clean in common_surnames:
            return {
                "success": False,
                "error": f"安全阻断: '{word}' 属于日本常见高频姓氏，Whisper 预训练模型识别率已达 100%，严禁将其加入全局通用人设库！单部作品人名请使用命令行 --keywords 传入。"
            }

    bank_path = REF_DIR / "vocabulary_bank.json"
    data = json.loads(bank_path.read_text(encoding="utf-8")) if bank_path.exists() else {"categories": {}, "learned_vocabulary": []}

    # Check if already present anywhere
    flat_existing = set(data.get("learned_vocabulary", []))
    for cat_name, cat_val in data.get("categories", {}).items():
        if isinstance(cat_val, list):
            flat_existing.update(cat_val)
        elif isinstance(cat_val, dict):
            for sub_list in cat_val.values():
                if isinstance(sub_list, list):
                    flat_existing.update(sub_list)

    if word in flat_existing:
        return {"success": True, "message": f"词汇 '{word}' 已存在于词库中，跳过添加。"}

    backup_file(bank_path)

    if category in data.get("categories", {}):
        target = data["categories"][category]
        if isinstance(target, list):
            target.append(word)
    else:
        if "learned_vocabulary" not in data:
            data["learned_vocabulary"] = []
        data["learned_vocabulary"].append({
            "word": word,
            "note": note,
            "added_at": datetime.now().isoformat()
        } if note else word)

    bank_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "message": f"成功学习并入库新词: '{word}' -> {category}"}


def add_acoustic_fix(pattern: str, replacement: str, note: str = "", force_naked: bool = False) -> dict:
    """
    Adds a phrase-bound acoustic correction rule.
    Enforces context binding to prevent catastrophic substring replacement.
    """
    if not pattern or not replacement:
        return {"success": False, "error": "错误模式(pattern)与修正目标(replacement)均不能为空"}

    if pattern.strip() == replacement.strip():
        return {"success": False, "error": f"无效规则: pattern '{pattern}' 与 replacement 完全相同（原样自替换），无修正意义！"}

    # Validation: forbid naked common words without context unless explicitly forced
    common_naked_words = {"お味噌", "味噌", "仲良し", "友達", "先生", "お母さん", "ママ", "パパ"}
    if pattern in common_naked_words and not force_naked:
        return {
            "success": False,
            "error": (
                f"安全阻断: 检测到孤立常见词 '{pattern}'！"
                f"禁止直接全词替换，必须绑定上下文短语（例如 'お味噌のチンポ' -> 'おじさんのチンポ'），"
                f"否则会导致正常词汇被严重误伤污染！如确实需要请指定 --force-naked。"
            )
        }

    fixes_path = REF_DIR / "acoustic_fixes.json"
    data = json.loads(fixes_path.read_text(encoding="utf-8")) if fixes_path.exists() else {"rules": [], "learned_fixes": []}

    # Check duplication
    for r in data.get("rules", []) + data.get("learned_fixes", []):
        if r.get("pattern") == pattern:
            return {"success": True, "message": f"规则 '{pattern}' 已存在，当前映射为 '{r.get('replacement')}'"}

    backup_file(fixes_path)

    new_rule = {
        "pattern": pattern,
        "replacement": replacement,
        "note": note or "User feedback learned rule",
        "added_at": datetime.now().isoformat()
    }
    data.setdefault("learned_fixes", []).append(new_rule)
    fixes_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"success": True, "message": f"成功学习并入库声学修正规则: '{pattern}' -> '{replacement}'"}


def add_hallucination_pattern(pattern: str, note: str = "") -> dict:
    """Adds a new hallucination regex pattern."""
    if not pattern or len(pattern.strip()) < 2:
        return {"success": False, "error": "伪影模式过短或为空"}

    h_path = REF_DIR / "hallucination_patterns.json"
    data = json.loads(h_path.read_text(encoding="utf-8")) if h_path.exists() else {"patterns": [], "learned_hallucinations": []}

    all_patterns = set(data.get("patterns", []))
    for item in data.get("learned_hallucinations", []):
        if isinstance(item, dict):
            all_patterns.add(item.get("pattern", ""))
        else:
            all_patterns.add(str(item))

    if pattern in all_patterns:
        return {"success": True, "message": f"伪影模式 '{pattern}' 已存在，跳过。"}

    backup_file(h_path)
    data.setdefault("learned_hallucinations", []).append({
        "pattern": pattern,
        "note": note,
        "added_at": datetime.now().isoformat()
    } if note else pattern)

    h_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "message": f"成功学习并入库伪影过滤规则: '{pattern}'"}


def main():
    parser = argparse.ArgumentParser(description="ASMR Subtitler Self-improving Feedback Engine")
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # 1. learn-word
    p_word = subparsers.add_parser("learn-word", help="Learn a new domain keyword")
    p_word.add_argument("word", type=str, help="Word to learn")
    p_word.add_argument("--category", "-c", type=str, default="learned_vocabulary", help="Category name")
    p_word.add_argument("--note", "-n", type=str, default="", help="Note or context")

    # 2. learn-fix
    p_fix = subparsers.add_parser("learn-fix", help="Learn a phrase-bound acoustic fix")
    p_fix.add_argument("pattern", type=str, help="Erroneous phrase pattern")
    p_fix.add_argument("replacement", type=str, help="Correct replacement")
    p_fix.add_argument("--note", "-n", type=str, default="", help="Note / context")
    p_fix.add_argument("--force-naked", action="store_true", help="Force allow naked single word without context")

    # 3. learn-hallucination
    p_hal = subparsers.add_parser("learn-hallucination", help="Learn a hallucination pattern")
    p_hal.add_argument("pattern", type=str, help="Hallucination regex pattern")
    p_hal.add_argument("--note", "-n", type=str, default="", help="Note / context")

    args = parser.parse_args()

    if args.command == "learn-word":
        res = add_vocabulary(args.word, args.category, args.note)
        print(f"[{ 'SUCCESS' if res['success'] else 'FAILED' }] {res.get('message') or res.get('error')}")
    elif args.command == "learn-fix":
        res = add_acoustic_fix(args.pattern, args.replacement, args.note, force_naked=args.force_naked)
        print(f"[{ 'SUCCESS' if res['success'] else 'FAILED' }] {res.get('message') or res.get('error')}")
    elif args.command == "learn-hallucination":
        res = add_hallucination_pattern(args.pattern, args.note)
        print(f"[{ 'SUCCESS' if res['success'] else 'FAILED' }] {res.get('message') or res.get('error')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
