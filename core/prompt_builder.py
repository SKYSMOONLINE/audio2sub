# -*- coding: utf-8 -*-
"""
ASMR Dynamic Initial Prompt Builder (Micro-theatre Architecture)
- Strictly avoids comma-separated vocabulary dumps ('語彙：A、B、C') which cause Whisper
  to hallucinate and regurgitate prompt words during quiet ASMR / Foley sound sections.
- Employs immersive, natural conversational framing dialogue (~40-80 tokens).
- Offers 9 fine-tuned Japanese ASMR genre presets.
- Seamlessly injects character names and context keywords into natural speech.
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def estimate_tokens(text: str) -> int:
    return int(len(text) * 1.2)

GENRE_PRESETS = {
    "manga_cafe": {
        "name": "漫咖/隔间忍耐类",
        "aliases": ["manga_cafe", "manga", "漫咖", "漫画喫茶", "漫喫", "ネカフェ", "ネットカフェ", "个室", "個室"],
        "framing": "これは漫喫での音声作品です。隣に聞こえないように耳元で囁いています。「しーっ、声出しちゃダメ。隣の人にバレちゃうよ…♡」",
    },
    "ntr_cheating": {
        "name": "NTR/偷情出轨类",
        "aliases": ["ntr_cheating", "ntr", "寝取られ", "浮気", "偷情", "出轨", "彼氏持ち", "人妻"],
        "framing": "これは音声作品の台詞です。彼氏に隠れて耳元で囁いています。「彼氏より気持ちいい？内緒でいっぱい出して…♡」",
    },
    "mesugaki_gyaru": {
        "name": "雌小鬼/辣妹挑逗类",
        "aliases": ["mesugaki_gyaru", "mesugaki", "gyaru", "雌小鬼", "辣妹", "小恶魔", "ざぁこ", "挑逗"],
        "framing": "これは音声作品の台詞です。小悪魔に耳元で囁き煽っています。「ざぁこ♡ おじさんもう限界？いっぱい出してね♡」",
    },
    "hypnosis_mind_control": {
        "name": "催眠/洗脑常识变换类",
        "aliases": ["hypnosis", "催眠", "洗脑", "マインドコントロール", "暗示", "常識改変"],
        "framing": "これは催眠音声作品の台詞です。耳元で暗示を囁きかけています。「私の言う通りにして…頭がぼーっとして気持ちよくなる…♡」",
    },
    "pure_love_childhood": {
        "name": "纯爱/幼驯染/温存姐姐类",
        "aliases": ["pure_love", "纯爱", "幼驯染", "幼馴染", "お姉ちゃん", "姐姐", "甘々", "癒し", "添い寝"],
        "framing": "これは純愛音声作品の台詞です。耳元で優しく甘えさせています。「大丈夫だよ…ずっと一緒にいようね。大好きだよ…♡」",
    },
    "sm_discipline": {
        "name": "调教/主奴/M女S男/女王类",
        "aliases": ["sm_discipline", "sm", "调教", "調教", "ご主人様", "奴隷", "M女", "命令"],
        "framing": "これは調教音声作品の台詞です。耳元で厳しく囁いています。「ご主人様って言いなさい。我慢できなくなったら許してあげる…♡」",
    },
    "infirmary_health_room": {
        "name": "保健室/校医/护士看护类",
        "aliases": ["infirmary", "保健室", "养护", "先生", "看護師", "ナース", "体温計"],
        "framing": "これは保健室での音声作品です。先生が耳元で優しく手当てしています。「静かにして…誰か入ってきたら大変だから、じっとしててね…♡」",
    },
    "onsen_hot_spring": {
        "name": "温泉/旅馆/露天混浴类",
        "aliases": ["onsen", "温泉", "露天風呂", "混浴", "旅館", "貸切風呂"],
        "framing": "これは温泉での音声作品です。耳元でしっとりと囁いています。「お湯加減どう？のぼせちゃうくらい気持ちよくしてあげるね…♡」",
    },
    "streaming_vtuber": {
        "name": "直播/Vtuber/网配类",
        "aliases": ["streaming", "直播", "vtuber", "生配信", "垢バン", "配信者", "リスナー"],
        "framing": "これは生配信での音声作品です。耳元で内緒の囁きをしています。「みんなには内緒だよ…垢バンされちゃうから静かにね…♡」",
    }
}

DEFAULT_BASE_FRAMING = "これは音声作品の台詞です。耳元で優しく囁きかけています。「ねぇ、気持ちいい？いっぱい出してね…♡」"

def match_genre(genre_hint: str) -> dict:
    if not genre_hint:
        return None
    g_lower = str(genre_hint).lower().strip()
    for g_key, g_info in GENRE_PRESETS.items():
        if g_lower == g_key or any(alias.lower() in g_lower or g_lower in alias.lower() for alias in g_info["aliases"]):
            return g_info
    return None

def build_initial_prompt(
    audio_context: str = "",
    budget: int = 135,
    custom_keywords: list = None,
    genre: str = None
) -> str:
    """
    Builds a clean, micro-theatre initial prompt.
    Avoids comma dumps; weaves context naturally.
    """
    # 1. Select framing
    g_preset = match_genre(genre)
    if not g_preset and audio_context:
        g_preset = match_genre(audio_context)

    base_frame = g_preset["framing"] if g_preset else DEFAULT_BASE_FRAMING

    # 2. Extract character names or custom keywords (strictly limit to 1-3 names)
    clean_kws = []
    if custom_keywords:
        for kw in custom_keywords:
            kw = str(kw).strip()
            if kw and len(kw) >= 2 and kw not in base_frame and kw not in clean_kws:
                # Filter out generic common nouns
                if kw not in ["漫喫", "音声", "作品", "台詞", "おじさん", "耳元"]:
                    clean_kws.append(kw)

    if clean_kws:
        kw_str = "、".join(clean_kws[:2])
        extended = f"{base_frame}「{kw_str}も一緒だよ…♡」"
        if estimate_tokens(extended) <= budget:
            return extended

    return base_frame

def main():
    parser = argparse.ArgumentParser(description="ASMR Dynamic Micro-theatre Prompt Builder")
    parser.add_argument("--context", "-c", type=str, default="", help="Audio filename or context")
    parser.add_argument("--genre", "-g", type=str, default=None, help="Preset genre")
    parser.add_argument("--keywords", "-k", nargs="*", default=[], help="Extra keywords")
    args = parser.parse_args()

    prompt = build_initial_prompt(audio_context=args.context, genre=args.genre, custom_keywords=args.keywords)
    print(prompt)

if __name__ == "__main__":
    main()
