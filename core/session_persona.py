# -*- coding: utf-8 -*-
"""
ASMR Session Persona Memory Manager
- Creates and maintains directory-level `_session_persona.json` for each album.
- Prevents cross-album contamination while remembering:
    1. Listener Persona (address: "大叔", forbidden addresses: "爷爷/老爷子/小老头")
    2. Character Cast (names, roles, voice tones, stereo ear channels)
    3. Album-specific scenario tags and context terms
- Exposes helper functions to initialize, load, update, and normalize listener addresses.
"""

import os
import sys
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PERSONA_FILENAME = "_session_persona.json"

DEFAULT_LISTENER_PERSONA = {
    "address": "大叔",
    "aliases": ["大叔", "叔叔", "怪大叔"],
    "forbidden_addresses": ["爷爷", "老爷子", "小老头", "老爷爷", "老头子", "老汉"]
}

# Regex to catch mistaken elderly addresses in Japanese ASMR translation context
ELDERLY_ADDRESS_REGEX = re.compile(r"(老爷爷|老爷子|小老头|老头子|爷爷|老汉)")


def get_persona_path(album_dir: Path) -> Path:
    return Path(album_dir).resolve() / PERSONA_FILENAME


def load_session_persona(album_dir: Path) -> dict:
    """Load existing _session_persona.json or return an empty template."""
    p_path = get_persona_path(album_dir)
    if p_path.is_file():
        try:
            return json.loads(p_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Persona] 警告: 读取 {p_path.name} 失败: {e}", file=sys.stderr)
    return {}


def save_session_persona(album_dir: Path, persona: dict) -> Path:
    """Save persona dict to _session_persona.json."""
    p_path = get_persona_path(album_dir)
    p_path.write_text(json.dumps(persona, ensure_ascii=False, indent=2), encoding="utf-8")
    return p_path


def init_session_persona(album_dir: Path, meta: Optional[dict] = None) -> dict:
    """
    Initializes or loads _session_persona.json.
    If it already exists, merges any newly provided metadata.
    """
    album_dir = Path(album_dir).resolve()
    existing = load_session_persona(album_dir)
    if existing:
        return existing

    meta = meta or {}
    char_names = meta.get("character_names", [])
    cv_list = meta.get("cv", [])
    tags = meta.get("scenario_tags", [])

    characters = []
    for idx, name in enumerate(char_names):
        cv = cv_list[idx] if idx < len(cv_list) else ""
        characters.append({
            "name": name,
            "cv": cv,
            "role": "主役/女主角" if idx == 0 else f"配角 {idx+1}",
            "ear_channel": "left" if idx == 0 and len(char_names) > 1 else ("right" if idx == 1 else "center"),
            "tone": "耳语/低吟"
        })

    # Default if no character detected
    if not characters and cv_list:
        for idx, cv in enumerate(cv_list):
            characters.append({
                "name": f"女主角 (CV: {cv})",
                "cv": cv,
                "role": "女主角",
                "ear_channel": "center",
                "tone": "耳语/低吟"
            })

    persona = {
        "album_name": album_dir.name,
        "circle": meta.get("circle", ""),
        "cv": cv_list,
        "listener_persona": dict(DEFAULT_LISTENER_PERSONA),
        "characters": characters,
        "context_terms": [c["name"] for c in characters if "name" in c and not c["name"].startswith("女主角")] + tags,
        "synopsis_notes": ""
    }

    save_session_persona(album_dir, persona)
    print(f"✔ [Persona] 已初始化专属角色档案: {PERSONA_FILENAME} (听众称谓: {persona['listener_persona']['address']})")
    return persona


def update_session_persona(
    album_dir: Path,
    new_terms: Optional[List[str]] = None,
    new_roles: Optional[List[dict]] = None,
    synopsis_notes: Optional[str] = None
) -> dict:
    """Updates context terms, character roles, or album synopsis notes."""
    album_dir = Path(album_dir).resolve()
    persona = load_session_persona(album_dir)
    if not persona:
        persona = init_session_persona(album_dir)

    updated = False
    if new_terms:
        current_terms = set(persona.get("context_terms", []))
        for t in new_terms:
            if t and t not in current_terms:
                persona.setdefault("context_terms", []).append(t)
                current_terms.add(t)
                updated = True

    if new_roles:
        existing_names = {c.get("name") for c in persona.get("characters", [])}
        for r in new_roles:
            if r.get("name") and r["name"] not in existing_names:
                persona.setdefault("characters", []).append(r)
                existing_names.add(r["name"])
                updated = True

    if synopsis_notes:
        persona["synopsis_notes"] = synopsis_notes
        updated = True

    if updated:
        save_session_persona(album_dir, persona)
    return persona


def normalize_listener_address(text: str, persona: Optional[dict] = None) -> Tuple[str, int]:
    """
    Normalizes elderly mistranslations (爷爷/老爷子/小老头) to the configured listener address ("大叔").
    Returns: (normalized_text, count_healed)
    """
    if not text:
        return text, 0

    target_addr = "大叔"
    if persona and "listener_persona" in persona:
        target_addr = persona["listener_persona"].get("address", "大叔")

    # Find matches
    matches = ELDERLY_ADDRESS_REGEX.findall(text)
    if not matches:
        return text, 0

    # Replace with target address
    normalized = ELDERLY_ADDRESS_REGEX.sub(target_addr, text)
    return normalized, len(matches)


def get_persona_prompt_keywords(album_dir: Path) -> List[str]:
    """Extracts valid character and context keywords for prompt builder injection."""
    persona = load_session_persona(album_dir)
    if not persona:
        return []

    keywords = []
    for char in persona.get("characters", []):
        name = char.get("name", "").strip()
        if name and not name.startswith("女主角") and len(name) <= 10:
            # Strip CV info if present
            clean_name = re.sub(r"\(CV:.*?\)", "", name).strip()
            if clean_name and clean_name not in keywords:
                keywords.append(clean_name)

    for term in persona.get("context_terms", []):
        if term and term not in keywords and len(term) <= 10:
            keywords.append(term)

    return keywords
