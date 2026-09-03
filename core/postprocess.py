# -*- coding: utf-8 -*-
"""
后处理模块
1. 本地日文杂音快筛：剔除韩文、伪影行首数字、纯标点符号行、视频套话元数据
2. 断句合并：把 whisper 碎片按「停顿 + 长度 + 标点语法」合并成自然句子
3. 语气词删减（中文）：删句中填充词、保行首情绪词、压缩情绪拟声、删纯语气行
"""
import re

# ---------- 本地日文杂音与伪影快筛 ----------
_KOREAN = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f\uA960-\uA97F\uD7B0-\uD7FF]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_LEADING_ARTIFACT = re.compile(r"^\s*(\d+[\.、\s\-]+|[\-\*・]\s*)")
_META_HALLUCINATIONS = re.compile(
    r"ご視聴ありがとうございました|ご覧いただきありがとう|チャンネル登録|高評価.*お願い|"
    r"お疲れ様でした.*次回|次回までお楽しみに|またご視聴下さい|Subtitles by|Translated by|bluetooth|"
    r"赤ちゃん|猫は|犬[もは]|ママ「|娘「|娘『|パパ「|耳元で甘[いく]|音声作品の台詞",
    re.IGNORECASE
)
_PUNCT_SYMBOLS_ONLY = re.compile(r"^[，。、！？!?…~～\s—\-－:：;；()（）「」『』\"'0-9]+$")


def sanitize_ja_line(text: str) -> str:
    """对单个日文字幕片段进行本地快筛与格式净化（0 API 耗时）。
    - 剔除行首数字编号伪影（如 "1. "、"2、"）
    - 过滤偶发韩文、西里尔乱码行与蓝牙设备伪影
    - 过滤 Whisper 网络视频套话元数据与片尾套话
    - 智能截断与压缩复读死循环（如あらら连发、左右手连发）
    - 过滤纯标点/纯符号行
    """
    if not text:
        return ""
    t = text.strip()
    if _KOREAN.search(t) or _CYRILLIC.search(t):
        return ""
    if _META_HALLUCINATIONS.search(t):
        return ""
    if _PUNCT_SYMBOLS_ONLY.match(t):
        return ""
    t = _LEADING_ARTIFACT.sub("", t).strip()
    if _PUNCT_SYMBOLS_ONLY.match(t):
        return ""

    # 压缩 2-6 字符的死循环片段（如 "左手、左手、左手、左手" -> "左手、左手"）
    t = re.sub(r"(.{2,6}?)\1{3,}", r"\1\1", t)
    # 压缩单字符重复连击（如 "あららららららら" -> "あらら"）
    t = re.sub(r"(.)\1{4,}", r"\1\1", t)

    # 若去重后有效字符极少且总长度较长，判定为胡言乱语死循环
    if len(t) > 12 and len(set(t)) <= 3:
        return ""

    return t.strip()


# ---------- 1. 断句合并（语法与标点感知） ----------

_TERMINATORS = ("。", "！", "!", "？", "?", "…")
_CONNECTIVES = ("て", "で", "けど", "から", "ので", "のに", "たら", "なら")


def merge_segments(segs, max_len=30, max_gap=1.5, max_span=8.0, term_gap=0.4, sanitize=True):
    """
    把 whisper 片段合并成适合字幕的自然句子。
    - sanitize=True: 自动执行本地轻量级杂音快筛
    - 遇到强终止标点且微停顿（>= term_gap 秒）→ 立即断句自立成行，避免过长拼接
    - 累计长度 > max_len 字 → 断句
    - 单句跨度过长（max_span）或普通停顿 >= max_gap → 断句
    返回 [(start, end, text), ...]
    """
    groups = []
    cur = ""
    cur_start = 0.0
    cur_end = 0.0
    for start, end, raw_text in segs:
        text = sanitize_ja_line(raw_text) if sanitize else (raw_text or "").strip()
        if not text:
            continue
        if cur:
            gap = start - cur_end
            has_terminated = any(cur.rstrip().endswith(t) for t in _TERMINATORS)
            is_connective = any(cur.rstrip().endswith(c) for c in _CONNECTIVES)

            should_break = False
            if has_terminated and gap >= term_gap:
                should_break = True
            elif not is_connective and (gap >= max_gap or len(cur) + len(text) > max_len or (end - cur_start) > max_span):
                should_break = True
            elif is_connective and (gap >= (max_gap * 1.3) or len(cur) + len(text) > max_len * 1.2 or (end - cur_start) > max_span):
                should_break = True

            if should_break:
                groups.append((cur_start, cur_end, cur))
                cur_start, cur = start, text
            else:
                cur += text
        else:
            cur_start, cur = start, text
        cur_end = end
    if cur:
        groups.append((cur_start, cur_end, cur))
    return groups


# ---------- 2. 语气词删减（中文） ----------

# 纯填充单字（无实义、可删）
FILLER_SET = set("嗯唔啊呀哎唉哦呃呜哈嘿诶噢")

# 情绪性拟声/笑声词（表达情感，保留但压缩连发）
EMOTIONAL_WORDS = ["呜呜", "啊啊", "嘿嘿", "嘻嘻", "哈哈", "呵呵", "哎呦", "哎呀", "嘿咻", "呼呼"]

# 连发压缩表：啊啊啊->啊啊, 呜呜呜->呜呜 ...（保留两字拟声）
_REPEAT_COMPRESS = [
    ("啊啊啊", "啊啊"), ("呜呜呜", "呜呜"), ("嘿嘿嘿", "嘿嘿"), ("嘻嘻嘻", "嘻嘻"),
    ("哈哈哈", "哈哈"), ("呵呵呵", "呵呵"), ("嗯嗯嗯", "嗯嗯"), ("唔唔唔", "唔唔"),
]

# 中文叠字连发压缩（如 去去去去 -> 去去，好好好好 -> 好好）
_REPEAT_PAT = re.compile(r"(.)\1{3,}")  # 同一字连续 4 次及以上


def _compress_dup(text: str) -> str:
    """把同一汉字 4 连以上压缩为 2 连，作为兜底（如 去去去去去 -> 去去）。"""
    while True:
        t2 = _REPEAT_PAT.sub(r"\1\1", text)
        if t2 == text:
            break
        text = t2
    return text


def _filler_alone(result: str) -> bool:
    """判断句子是否只剩语气词/标点/语气性内容（无实义文字）。"""
    clean = re.sub(r"[，。、！？…~～\s\-—:：;；()（）「」『』\"'0-9]", "", result)
    if not clean:
        return False
    for w in EMOTIONAL_WORDS:
        if w in clean:
            return False
    return all(ch in FILLER_SET for ch in clean)


def _is_pure_filler(piece: str) -> bool:
    """判断片段是否为纯填充语气词（去标点后全为填充字）。"""
    clean = re.sub(r"[，。、！？…~～\s\-—－:：;；()（）「」『』\"']", "", piece)
    if not clean:
        return False
    if piece.rstrip().endswith("?"):
        return False  # 疑问保留
    # 情绪词视为非纯填充（保留）
    for w in EMOTIONAL_WORDS:
        if w in clean:
            return False
    return all(ch in FILLER_SET for ch in clean)


def clean_fillers_cn(text: str, remove_pure_lines=True) -> str:
    """
    中文语气词删减（zh_clean 精简模式的后处理）。
    规则（与翻译端风格约定一致，源头删为主、这里只做兜底）：
    - 叠字连发压缩（去去去去->去去、啊啊啊->啊啊）
    - 整行只剩语气/拟声：保留“嗯？”式疑问回应与 呜呜/嘿嘿/哈哈 等情绪拟声行，
      其余纯填充行（嗯、啊、嗯嗯）返回空串，由上层整行删除
    - 含实义内容的行：删掉以标点/破折号/行首行尾为界的孤立语气词（嗯/啊/诶…），
      不删句尾粘着语气的实词句（“好舒服啊”保留），不拆“嗯？好”这类疑问接话
    """
    # 1) 叠字连发压缩
    t = _compress_dup(text)
    for a, b in _REPEAT_COMPRESS:
        t = t.replace(a, b)

    # 2) 整行纯语气/拟声判定
    if _filler_alone(t):
        # 纯填充行：只保留疑问回应与情绪拟声行，其余删除
        if "？" in t or "?" in t:
            return t
        for w in EMOTIONAL_WORDS:
            if w in t:
                return t
        return ""

    # 3) 有实义内容：删除以标点/空格/行首行尾为界的孤立填充串。
    #    “？”不算删除边界 → “嗯？”疑问接话不受影响。
    t = re.sub(
        r"(^|[，。、！…~～\s—\-:：;；（）「」『』\"'()])"
        r"([嗯唔啊呀哎唉哦呃呜哈嘿诶噢]+)"
        r"(?=[，。、！…~～\s—\-:：;；（）「」『』\"'()]|$)",
        lambda m: m.group(1), t)
    # 4) 清理残留：
    #    - whisper 分段编号混入（行首 “1 啊啊” 这类），去行首 “数字+分隔” 伪影
    #    - 行首标点/破折号、标点+破折号粘连、重复/混合标点、行尾悬空
    t = re.sub(r"^\d+[.、]?\s*(?=[^\d])", "", t)
    t = re.sub(r"^[，、\s—\-～~…]+", "", t)
    t = re.sub(r"[—\-]{2,}", "——", t)                        # 残留破折号连串归并为——
    t = re.sub(r"(?<=[，。、！？…~～])[—\-～~…]+", "", t)      # 标点后跟的破折号（删除语气词后残留）
    t = re.sub(r"[，。、！？…~～]{2,}", lambda m: m.group(0)[0], t)  # 混合/重复标点取首个
    t = re.sub(r"[，、—\-～~…]+$", "", t).strip()
    return t


def build_cn_clean_lines(groups):
    """
    对翻译后的中文句子应用语气词删减，返回 [(start, end, text), ...]
    只保留有实义的行（删掉纯语气行）。
    """
    result = []
    for start, end, text in groups:
        cleaned = clean_fillers_cn(text)
        if cleaned:
            result.append((start, end, cleaned))
    return result
