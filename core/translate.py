# -*- coding: utf-8 -*-
"""
翻译模块：日语（或其他语言）文本 -> 中文

- 主模型 deepseek-v4-flash（推理模型），失败自动兜底 deepseek-chat
- 逐行翻译 + 60s 超时，防 API 挂起
- 逐句风格规则（PER_LINE_STYLE）：删无实义语气词、压缩笑声拟声，输出简短口语
- 整篇术语一致性校正（glossary_pass）：逐句翻完后再做一次整篇统一，
  解决“大叔 / 叔叔 / 爷爷”这类称呼前后不一致、人名前后不一致的问题
"""
import re

from openai import OpenAI

import config

# ---------- 逐句翻译的风格规则（作为 system 消息，比塞进 user 消息更稳定） ----------
PER_LINE_STYLE = """你是成人向日语音声作品的中文译者，把女方说给年长男伴听的口语台词翻成简体中文。
用户发来一句日语台词，你只输出对应的一句译文。

规则：
1. 简短自然：能省的字就省，不逐词硬翻、不堆砌修饰、不用生硬的书面语。
2. 无实义的语气填充词（んっ、あっ、えっ、うーん、んー、はぁ、ふぅ、あの、えっと、まぁ 等）不要译出来，直接略过。
   只有语气词承担表意作用时才译，例如：疑问“え？”“ん？”→“嗯？”“诶？”；拒绝“いや”→“不要”；
   单独成句表示肯定的“うん”→“嗯”。
3. 笑声 / 情绪拟声只保留最多 2 个重复：哈哈哈→哈哈、ふふふ→嘿嘿、呜呜呜→呜呜、あはは→哈哈。
4. 句尾语气（～、ね、よ、わ、なぁ）能不译就不译，不要每句都加“呢 / 哦 / 吧 / 啊”，避免满屏语气字。
5. 称呼用最常用的自然口语：おじさん→大叔；同一句内同一人物用词要一致。
6. 只输出译文本身。不要解释、不要加引号、不要加行号。"""

# ---------- 术语统一偏好（glossary_pass 阶段用；某作品里若确实是别的对象可自行修改） ----------
TERM_HINTS = """术语统一偏好（如与剧情明显不符则以剧情为准）：
- おじさん / おじいちゃん / おじいさん：若都指女方对话中的那位年长男伴本人，统一为“大叔”。
- お姉さん → 姐姐；おばさん → 阿姨；お兄さん → 哥哥。
- 人名（ちえ、ちーちゃん、千恵 等）统一为出现最多、最自然的那个译法。"""

# 一致性校正分块大小（行数）。整篇超过该行数时分段处理，避免一次请求过长。
GLOSSARY_CHUNK = 150


def make_client(api_key: str) -> OpenAI:
    """创建带超时的 OpenAI 兼容客户端（指向 DeepSeek）"""
    return OpenAI(api_key=api_key, base_url=config.LLM_BASE_URL, timeout=60)


def translate_lines(client: OpenAI, lines, llm="deepseek-v4-flash",
                    progress_cb=None, stop_event=None) -> list:
    """
    逐行翻译 lines（日语原文），返回与输入等长的中文译文列表。
    - 每行先试主模型（llm），失败/空则用 deepseek-chat 兜底
    - progress_cb(done, total) 进度回调
    """
    fallback = "deepseek-chat"
    out = []
    total = len(lines)
    for i, txt in enumerate(lines):
        if stop_event and stop_event.is_set():
            break
        got = ""
        for m in (llm, fallback):
            try:
                r = client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": PER_LINE_STYLE},
                        {"role": "user", "content": txt},
                    ],
                    max_tokens=2000,
                    temperature=0.3,
                )
                c = (r.choices[0].message.content or "").strip()
                if c:
                    got = c
                    break
            except Exception:
                continue
        out.append(got)
        if progress_cb and ((i + 1) % 5 == 0 or (i + 1) == total):
            progress_cb(i + 1, total)
    return out


# ---------- 听写后噪音行清洗 ----------

def obvious_noise(text: str) -> bool:
    """启发式快筛：明显不是有效日文台词的行（空 / 韩文乱码 / 纯符号 / 视频套话）。"""
    from .postprocess import sanitize_ja_line
    return not bool(sanitize_ja_line(text))


DENOISE_SYSTEM = """你是日语音声作品字幕的预处理助手，负责筛掉语音识别产生的“噪音行”。
用户会发来带行号的日文文本，其中可能混入：非语音的呻吟/喘息/抽气声（如 んっ、ぷっ、かっ、くっ、ふっ、あぁ、ぅ 的连续堆叠）、背景声/游戏音效/哼唱被误听成的词（甚至混入韩文、英文、乱码）、一整段没有词汇内容的拟声。
请逐行判断它是“应保留的真实台词”还是“噪音”：
- 保留 K：含有实义词汇的句子、称呼（大叔/おじさん等）、有明确表意的回应；即使是短句如“嗯？”“ん？”“喂？”“好”“等下”也算有表意，保留。
- 丢弃 N：整行只是无实义呻吟/抽气/单音节重复堆叠；整行是误听乱码/韩文/背景声，读不出有意义的话。
拿不准时倾向保留（K）。不要改写任何文本。
严格按输入行号逐行输出：序号|K 或 序号|N，一行一条，不要输出任何其它内容。"""


def denoise_lines(client, texts, llm="deepseek-v4-flash", progress_cb=None,
                  stop_event=None) -> list:
    """
    清洗 texts（日语台词），返回与输入等长的 bool 列表（True=保留）。
    - 启发式快筛先行（空/韩文乱码 → 丢弃），其余交给 LLM 批量判定。
    - LLM 调用失败或输出不完整 → 该段退回启发式结果（即宁留勿删）。
    """
    keep = [not obvious_noise(t) for t in texts]
    pending = [i for i, k in enumerate(keep) if k]
    if not pending:
        return keep
    for start in range(0, len(pending), GLOSSARY_CHUNK):
        if stop_event and stop_event.is_set():
            break
        part = pending[start:start + GLOSSARY_CHUNK]
        body = "\n".join(f"{j + 1}|{texts[i]}" for j, i in enumerate(part))
        for m in (llm, "deepseek-chat"):
            try:
                r = client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": DENOISE_SYSTEM},
                        {"role": "user", "content": body},
                    ],
                    max_tokens=4000,
                    temperature=0.0,
                )
                c = (r.choices[0].message.content or "").strip()
                marks = {}
                for ln in c.splitlines():
                    mm = re.match(r"^(\d+)\s*[|｜]\s*([KN])$", ln.strip())
                    if mm:
                        marks[int(mm.group(1))] = mm.group(2)
                if set(marks) == set(range(1, len(part) + 1)):
                    for j, i in enumerate(part):
                        keep[i] = marks[j + 1] == "K"
                    break  # 该段判定成功
            except Exception:
                continue
        # 两模型都失败/解析不全：本段退回启发式结果，继续下一段
    if progress_cb:
        progress_cb(1, 1)
    return keep


# ---------- 整篇术语一致性校正 ----------

def _parse_marked(text: str) -> dict:
    """解析“编号|译文”行 -> {编号: 译文}。容忍代码块围栏、多余空行等杂质。"""
    out = {}
    for ln in text.splitlines():
        s = ln.strip().lstrip("`").rstrip("`").strip()
        m = re.match(r"^(\d+)\s*[|｜]\s*(.*)$", s)
        if m:
            body = m.group(2).strip()
            # 防御：若模型回显了“原文 || 译文”，只取最后一个 || 之后的译文
            if "||" in body:
                body = body.split("||")[-1].strip()
            out[int(m.group(1))] = body
    return out


def glossary_pass(client: OpenAI, pairs, llm="deepseek-v4-flash", stop_event=None) -> list:
    """
    整篇一致性校正：把 (日文原文, 当前译文) 的完整列表交给 LLM，
    统一称呼 / 人名 / 专名，返回校正后的译文列表（与输入等长、顺序一致）。

    任何一段失败（调用失败 / 行数对不上 / 编号不完整）就跳过该段，保留原译文。
    """
    total = len(pairs)
    if total == 0:
        return [z for _, z in pairs]
    corrected = [z for _, z in pairs]

    header = (
        "你是成人向日语音声作品字幕的术语校对。下面是一段字幕，每行格式：\n"
        "行号|日文原文 || 当前中文译文\n"
        "只做以下工作：\n"
        "1. 术语统一：日文原文指代同一对象 / 同一人的称呼、人名、专名，全篇必须用同一个中文词。"
        "例如原文反复出现おじさん，译文不能一会儿“大叔”一会儿“叔叔”；"
        "おじいちゃん / おじいさん 如果语境中指的就是那位年长男伴本人，也要一并统一成同一个译法。\n"
        f"{TERM_HINTS}\n"
        "2. 只做术语统一和轻微润色，不要改写句子结构，不要增删内容。\n"
        "3. 保持行数、行号与顺序完全不变。\n"
        "4. 某行译文若为空（|| 后无内容），输出也留空。\n"
        "5. 输出的“行号|译文”中，译文列只写统一后的中文译文本身："
        "严禁把日文原文一并抄回，严禁出现“ || ”分隔符，严禁加解释或行号以外的前缀。\n"
        "严格逐行输出：行号|统一后的译文，一行一条，不要任何解释。"
    )

    for start in range(0, total, GLOSSARY_CHUNK):
        if stop_event and stop_event.is_set():
            break
        part = pairs[start:start + GLOSSARY_CHUNK]
        body = "\n".join(f"{i + 1}|{j} || {z}" for i, (j, z) in enumerate(part))
        for m in (llm, "deepseek-chat"):
            try:
                r = client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": header},
                        {"role": "user", "content": body},
                    ],
                    max_tokens=8000,
                    temperature=0.0,
                )
                c = (r.choices[0].message.content or "").strip()
                parsed = _parse_marked(c)
                if parsed and set(parsed) == set(range(1, len(part) + 1)):
                    for k, v in parsed.items():
                        corrected[start + k - 1] = v
                    break  # 该段校正成功
            except Exception:
                continue
        # 若两种模型都失败：本段保留原译文，继续下一段
    return corrected
