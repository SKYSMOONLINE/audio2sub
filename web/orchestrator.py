from pathlib import Path
import threading

import config
from core.postprocess import clean_fillers_cn, merge_segments
from core.subtitles import fmt_ts, output_name, save_lrc
from core.transcribe import load_model, transcribe_audio
from core.translate import denoise_lines, glossary_pass, make_client, obvious_noise, translate_lines

_MODEL_CACHE = {}
_MODEL_LOCK = threading.Lock()


def _get_model(name, compute, log=None):
    log = log if callable(log) else (lambda msg, level="info": None)
    with _MODEL_LOCK:
        if name not in _MODEL_CACHE:
            _MODEL_CACHE[name] = load_model(name, compute, log=lambda m: log("  " + m, "info"))
        return _MODEL_CACHE[name]


def _write(lines, audio, out_dir, suffix, log):
    if not lines:
        log("  ⚠ 该音频没有可输出的字幕行，跳过", "warn")
        return None
    path = output_name(audio, suffix=suffix, out_dir=out_dir)
    save_lrc(lines, path)
    log(f"  ✔ 已保存: {path.name}（{len(lines)} 行）")
    return {"name": path.name, "lines": len(lines), "path": str(path)}


def run_job(job, manager):
    p = job.payload
    files = p["audio_paths"]
    log = lambda msg, level="info": manager.log(job, msg, level)

    log(f"共 {len(files)} 个音频文件")
    manager.stage(job, "load_model", "加载模型…")
    model = _get_model(p["model"], "int8_float16", log=log)
    job.stats["device"] = str(getattr(model, "device", None) or "int8")
    log(f"模型已加载: {p['model']}（device={job.stats['device']}）")

    client = None
    if p["mode"] != "jp_raw":
        key = config.load_api_key()
        if not key:
            raise RuntimeError("需要 DeepSeek API Key（未翻译模式除外）")
        client = make_client(key)
        log(f"DeepSeek 客户端就绪（{p['llm']}）")

    prompt = ""
    if p.get("use_prompt", True):
        if config.DOMAIN_PROMPT_FILE.exists():
            prompt = config.DOMAIN_PROMPT_FILE.read_text(encoding="utf-8")
            log(f"已加载领域提示词（{len(prompt)} 字）")
        else:
            log("⚠ 未找到 domain_prompt.txt，跳过提示词", "warn")

    for file_index, audio in enumerate(files, 1):
        if job.stop_event.is_set():
            return
        job.payload["file_index"] = file_index
        job.payload["file_name"] = Path(audio).name
        log(f"\n[{file_index}/{len(files)}] {Path(audio).name}")
        manager.stage(job, "transcribe", Path(audio).name, file_index, len(files))
        segs, lang = transcribe_audio(
            model, audio, p["lang"], prompt,
            progress_cb=lambda msg: log("  " + msg),
        )
        job.stats["lang_detected"] = lang
        job.stats["segments"] += len(segs)
        if job.stop_event.is_set():
            return

        manager.stage(job, "merge", "断句…")
        groups = merge_segments(segs)
        job.stats["sentences"] += len(groups)
        log(f"  断句为 {len(groups)} 句")

        if p.get("denoise", True) and groups:
            before = len(groups)
            manager.stage(job, "denoise", "清洗噪音行…")
            if client:
                keep = denoise_lines(
                    client, [text for _, _, text in groups], p["llm"],
                    progress_cb=lambda done, total: manager.stage(job, "denoise", "清洗噪音行…", done, total),
                    stop_event=job.stop_event,
                )
                groups = [group for group, keep_line in zip(groups, keep) if keep_line]
            else:
                groups = [group for group in groups if not obvious_noise(group[2])]
            job.stats["removed_noise"] += before - len(groups)
            log(f"  清洗噪音行：{before} -> {len(groups)} 句")
        if job.stop_event.is_set():
            return

        if p["mode"] == "jp_raw":
            lines = [fmt_ts(start) + text for start, _, text in groups]
            result = _write(lines, audio, p["out_dir"], "", log)
            if result:
                job.results.append({"audio": Path(audio).name, "lrcs": [result]})
            continue

        ja_lines = [text for _, _, text in groups]
        manager.stage(job, "translate", "翻译 0/{0}".format(len(ja_lines)), 0, len(ja_lines))
        zh_lines = translate_lines(
            client, ja_lines, p["llm"],
            progress_cb=lambda done, total: manager.stage(job, "translate", f"翻译 {done}/{total}", done, total),
            stop_event=job.stop_event,
        )
        if job.stop_event.is_set():
            return
        failed = sum(1 for text in zh_lines if not text)
        job.stats["translated_ok"] += len(zh_lines) - failed
        log(f"  翻译完成，{len(zh_lines) - failed}/{len(zh_lines)} 句成功")

        if p.get("glossary", True) and failed == 0 and zh_lines:
            manager.stage(job, "glossary", "术语统一…")
            zh_lines = glossary_pass(
                client, list(zip(ja_lines, zh_lines)), p["llm"], stop_event=job.stop_event
            )
            if job.stop_event.is_set():
                return
            log("  术语统一完成（称呼/人名前后一致）")

        data = [(start, end, ja, zh) for (start, end, ja), zh in zip(groups, zh_lines)]
        bilingual = [fmt_ts(start) + (f"{zh}（{ja}）" if zh else ja) for start, _, ja, zh in data]
        chinese = [fmt_ts(start) + (zh if zh else ja) for start, _, ja, zh in data]
        clean = []
        for start, _, _, zh in data:
            cleaned = clean_fillers_cn(zh) if zh else ""
            if cleaned:
                clean.append(fmt_ts(start) + cleaned)

        outputs = []
        if p["mode"] == "zh_clean":
            outputs.append(_write(clean, audio, p["out_dir"], "", log))
        elif p["mode"] == "zh":
            outputs.append(_write(chinese, audio, p["out_dir"], "", log))
        elif p["mode"] == "bilingual":
            outputs.append(_write(bilingual, audio, p["out_dir"], "", log))
        elif p["mode"] == "both":
            outputs.append(_write(clean, audio, p["out_dir"], "", log))
            outputs.append(_write(bilingual, audio, p["out_dir"], "双语", log))
        outputs = [result for result in outputs if result]
        if outputs:
            job.results.append({"audio": Path(audio).name, "lrcs": outputs})
        manager.stage(job, "write", f"写出 {file_index}/{len(files)}", file_index, len(files))

    log("\n全部完成 ✔")
