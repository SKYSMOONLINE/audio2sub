# -*- coding: utf-8 -*-
"""
audio2sub —— 音频字幕小工具（Tkinter GUI）
流水线：音频 → Whisper 听译（带领域提示词）→ 断句 → DeepSeek 翻译 → 语气词删减 → LRC

运行方式（Windows）：
    D:\\ASMR\\openlrc\\.venv\\Scripts\\python.exe app.py
或双击 start.bat
"""
import os
import queue
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 确保能 import config 与 core
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import config  # noqa: E402
from core.transcribe import load_model, transcribe_audio  # noqa: E402
from core.postprocess import merge_segments, clean_fillers_cn  # noqa: E402
from core.translate import (  # noqa: E402
    make_client, translate_lines, glossary_pass,
    denoise_lines, obvious_noise,
)
from core.subtitles import fmt_ts, save_lrc  # noqa: E402

# ---------- 输出模式 ----------
MODES = [
    ("zh_clean", "精简中文（删语气词，推荐阅读）"),
    ("zh", "纯中文（保留语气词）"),
    ("bilingual", "双语：中文（日文原文）"),
    ("both", "双语 + 精简中文（两份）"),
    ("jp_raw", "不翻译 · 日语原文"),
]

AUDIO_EXTS = config.AUDIO_EXTS


# ============ 核心流水线 ============
def pipeline(files, opts, log, progress, stop_event):
    """files: 音频路径列表; opts: dict; log(msg)/progress(done,total,stage) 回调"""
    log(f"共 {len(files)} 个音频文件")
    model = None
    client = None
    try:
        model = load_model(opts["model"], opts["compute"], log=lambda m: log("  " + m))
        log(f"模型已加载: {opts['model']}（device={getattr(model, 'device', '?')}）")
    except Exception as e:
        log(f"✗ 模型加载失败: {e}")
        return False
    if opts["mode"] != "jp_raw":
        key = opts["api_key"]
        if not key:
            log("✗ 需要 DeepSeek API Key（未翻译模式除外）")
            return False
        client = make_client(key)
        log(f"DeepSeek 客户端就绪（{opts['llm']}）")

    prompt = ""
    if opts["use_prompt"]:
        pf = config.DOMAIN_PROMPT_FILE
        if pf.exists():
            prompt = pf.read_text(encoding="utf-8")
            log(f"已加载领域提示词（{len(prompt)} 字）")
        else:
            log("⚠ 未找到 domain_prompt.txt，跳过提示词")

    for fi, audio in enumerate(files, 1):
        if stop_event.is_set():
            log("已停止")
            return False
        log(f"\n[{fi}/{len(files)}] {Path(audio).name}")
        # 1. 听译
        try:
            segs, lang = transcribe_audio(
                model, audio, opts["lang"], prompt,
                progress_cb=lambda m: log("  " + m),
            )
        except Exception as e:
            log(f"✗ 听译失败: {e}")
            continue
        # 2. 断句
        groups = merge_segments(segs)
        log(f"  断句为 {len(groups)} 句")
        # 2.5 噪音行清洗（呻吟/误听/背景声/乱码）
        if opts.get("denoise", True) and groups:
            n_before = len(groups)
            if client is not None:
                keep = denoise_lines(
                    client, [t for _, _, t in groups], opts["llm"],
                    progress_cb=lambda d, t: progress(d, t, "去噪"),
                )
                groups = [g for g, k in zip(groups, keep) if k]
            else:
                # 未提供 key（jp_raw 免 key 模式）：仅启发式快筛
                groups = [g for g in groups if not obvious_noise(g[2])]
            log(f"  清洗噪音行：{n_before} -> {len(groups)} 句")
        if opts["mode"] == "jp_raw":
            ja = [t for _, _, t in groups]
            lines = [fmt_ts(s) + t for (s, _, _), t in zip(groups, ja)]
            _write(lines, audio, opts, log, fi)
            continue
        # 3. 翻译
        ja_lines = [t for _, _, t in groups]
        zh_lines = translate_lines(
            client, ja_lines, opts["llm"],
            progress_cb=lambda d, t: progress(d, t, "翻译"),
        )
        n_fail = sum(1 for z in zh_lines if not z)
        log(f"  翻译完成，{len(zh_lines) - n_fail}/{len(zh_lines)} 句成功")
        if opts.get("glossary", True) and n_fail == 0 and zh_lines:
            zh_lines = glossary_pass(
                client, list(zip(ja_lines, zh_lines)), opts["llm"],
            )
            log(f"  术语统一完成（称呼/人名前后一致）")
        data = [(s, e, j, z) for (s, e, j), z in zip(groups, zh_lines)]

        # 4. 生成字幕（按模式）
        lines_bi = [fmt_ts(s) + (f"{z}（{j}）" if z else j) for s, _, j, z in data]
        lines_zh = [fmt_ts(s) + (z if z else j) for s, _, j, z in data]
        lines_clean = []
        for s, _, _, z in data:
            zc = clean_fillers_cn(z) if z else ""
            if zc:
                lines_clean.append(fmt_ts(s) + zc)

        mode = opts["mode"]
        if mode == "zh_clean":
            _write(lines_clean, audio, opts, log, fi, suffix="")
        elif mode == "zh":
            _write(lines_zh, audio, opts, log, fi, suffix="")
        elif mode == "bilingual":
            _write(lines_bi, audio, opts, log, fi, suffix="")
        elif mode == "both":
            _write(lines_clean, audio, opts, log, fi, suffix="")
            _write(lines_bi, audio, opts, log, fi, suffix="双语")
        progress(fi, len(files), "完成")
    log("\n全部完成 ✔")
    return True


def _write(lines, audio, opts, log, idx, suffix=""):
    """写出 LRC（自动处理重名）"""
    if not lines:
        log("  ⚠ 该音频没有可输出的字幕行，跳过")
        return
    out_dir = Path(opts["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(audio).stem
    name = f"{stem}{('.' + suffix) if suffix else ''}.lrc"
    out = out_dir / name
    n = 1
    while out.exists():
        out = out_dir / f"{stem}{('.' + suffix) if suffix else ''}_{n}.lrc"
        n += 1
    save_lrc(lines, out)
    log(f"  ✔ 已保存: {out.name}（{len(lines)} 行）")


# ============ GUI ============
class App:
    def __init__(self, root):
        self.root = root
        root.title("audio2sub - 音频字幕生成")
        root.geometry("760x620")
        root.minsize(680, 560)

        self.audio_files = []
        self.worker = None
        self.stop_event = threading.Event()
        self.log_q = queue.Queue()

        self._build_ui()
        self.root.after(100, self._drain_log)

    # ----- 界面 -----
    def _build_ui(self):
        pad = {"padx": 8, "pady": 3}
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        # 音频源
        f_src = ttk.LabelFrame(main, text="① 音频来源", padding=8)
        f_src.pack(fill="x", **pad)
        self.src_type = tk.StringVar(value="file")
        ttk.Radiobutton(f_src, text="单个文件", variable=self.src_type,
                        value="file", command=self._on_src_type).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(f_src, text="整个文件夹", variable=self.src_type,
                        value="dir", command=self._on_src_type).grid(row=0, column=1, sticky="w")
        self.btn_pick = ttk.Button(f_src, text="选择…", command=self._pick)
        self.btn_pick.grid(row=0, column=2, padx=8)
        self.lbl_src = ttk.Label(f_src, text="未选择", foreground="#666")
        self.lbl_src.grid(row=0, column=3, sticky="w")

        # 听译设置
        f_asr = ttk.LabelFrame(main, text="② 听译设置", padding=8)
        f_asr.pack(fill="x", **pad)
        ttk.Label(f_asr, text="模型:").grid(row=0, column=0, sticky="w")
        self.model_var = tk.StringVar(value=self._default_model_label())
        cb_model = ttk.Combobox(f_asr, textvariable=self.model_var, state="readonly",
                                values=list(config.MODEL_LABELS.keys()), width=28)
        cb_model.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(f_asr, text="语言:").grid(row=0, column=2, sticky="w")
        self.lang_var = tk.StringVar(value="日语")
        ttk.Combobox(f_asr, textvariable=self.lang_var, state="readonly",
                     values=list(config.LANG_LABELS.keys()), width=8).grid(row=0, column=3, sticky="w", padx=4)
        self.use_prompt = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_asr, text="启用领域提示词（音声词表）",
                        variable=self.use_prompt).grid(row=1, column=0, columnspan=2, sticky="w")
        self.denoise_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_asr, text="清除噪音行（呻吟/误听/背景声，需API）",
                        variable=self.denoise_var).grid(row=1, column=2, columnspan=3, sticky="w")

        # 翻译设置
        f_tr = ttk.LabelFrame(main, text="③ 翻译设置（DeepSeek）", padding=8)
        f_tr.pack(fill="x", **pad)
        ttk.Label(f_tr, text="API Key:").grid(row=0, column=0, sticky="w")
        self.key_var = tk.StringVar(value=config.load_api_key())
        e_key = ttk.Entry(f_tr, textvariable=self.key_var, width=40, show="*")
        e_key.grid(row=0, column=1, sticky="w", padx=4)
        self.save_key = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_tr, text="记住", variable=self.save_key).grid(row=0, column=2)
        ttk.Label(f_tr, text="翻译模型:").grid(row=1, column=0, sticky="w")
        self.llm_var = tk.StringVar(value=config.DEFAULT_LLM)
        ttk.Combobox(f_tr, textvariable=self.llm_var, state="readonly",
                     values=config.LLM_CHOICES, width=18).grid(row=1, column=1, sticky="w", padx=4)
        self.glossary_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_tr, text="术语统一（整篇称呼/人名一致，需二次调用）",
                        variable=self.glossary_var).grid(row=2, column=1, columnspan=3, sticky="w")

        # 输出
        f_out = ttk.LabelFrame(main, text="④ 输出", padding=8)
        f_out.pack(fill="x", **pad)
        ttk.Label(f_out, text="模式:").grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="zh_clean")
        for i, (key, label) in enumerate(MODES):
            ttk.Radiobutton(f_out, text=label, value=key,
                            variable=self.mode_var).grid(row=0 + i // 3, column=1 + i % 3, sticky="w", padx=4)
        self.out_dir_var = tk.StringVar(value="")
        ttk.Label(f_out, text="输出目录:").grid(row=2, column=0, sticky="w")
        ttk.Entry(f_out, textvariable=self.out_dir_var, width=40).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Button(f_out, text="选择…", command=self._pick_out).grid(row=2, column=2)

        # 控制
        f_ctl = ttk.Frame(main)
        f_ctl.pack(fill="x", **pad)
        self.btn_start = ttk.Button(f_ctl, text="▶ 开始处理", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(f_ctl, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.prog = ttk.Progressbar(f_ctl, mode="determinate")
        self.prog.pack(side="right", fill="x", expand=True)

        # 日志
        f_log = ttk.LabelFrame(main, text="日志", padding=4)
        f_log.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(f_log, height=14, font=("Consolas", 9), state="disabled",
                           wrap="word")
        sb = ttk.Scrollbar(f_log, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)

        # 状态栏
        self.status = ttk.Label(main, text="就绪", anchor="w")
        self.status.pack(fill="x")

    def _default_model_label(self):
        for label, key in config.MODEL_LABELS.items():
            if key == config.DEFAULT_MODEL:
                return label
        return list(config.MODEL_LABELS.keys())[0]

    def _on_src_type(self):
        self.audio_files = []
        self.lbl_src.config(text="未选择")

    def _pick(self):
        if self.src_type.get() == "file":
            p = filedialog.askopenfilename(
                title="选择音频", filetypes=[("音频", "*.wav *.mp3 *.flac *.m4a *.ogg *.aac *.wma"),
                                              ("所有文件", "*.*")])
            if p:
                self.audio_files = [p]
                self.lbl_src.config(text=Path(p).name)
        else:
            d = filedialog.askdirectory(title="选择含音频的文件夹")
            if d:
                files = sorted(
                    f for f in Path(d).iterdir()
                    if f.suffix.lower() in AUDIO_EXTS and f.is_file())
                self.audio_files = files
                self.lbl_src.config(text=f"{len(files)} 个音频 @ {d}")

    def _pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_dir_var.set(d)

    def _log(self, msg):
        self.log_q.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.txt.configure(state="normal")
                self.txt.insert("end", msg + "\n")
                self.txt.see("end")
                self.txt.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    # ----- 启动/停止 -----
    def _start(self):
        if not self.audio_files:
            messagebox.showwarning("提示", "请先选择音频文件或文件夹")
            return
        mode = self.mode_var.get()
        api_key = self.key_var.get().strip()
        if self.save_key.get() and api_key:
            config.save_api_key(api_key)
        out_dir = self.out_dir_var.get().strip() or str(Path(self.audio_files[0]).parent)

        opts = {
            "model": config.MODEL_LABELS[self.model_var.get()],
            "lang": config.LANG_LABELS[self.lang_var.get()],
            "compute": config.DEFAULT_COMPUTE,
            "use_prompt": self.use_prompt.get(),
            "denoise": self.denoise_var.get(),
            "llm": self.llm_var.get(),
            "glossary": self.glossary_var.get(),
            "api_key": api_key,
            "mode": mode,
            "out_dir": out_dir,
        }
        self.stop_event.clear()
        self.prog.config(value=0, maximum=max(len(self.audio_files), 1))
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status.config(text="处理中…")

        def run():
            def log(m):
                self._log(m)
            def progress(done, total, stage=""):
                self.root.after(0, lambda: self._set_progress(done, total, stage))
            try:
                pipeline(self.audio_files, opts, log, progress, self.stop_event)
            except Exception as e:
                log(f"✗ 出错: {e}")
            finally:
                self.root.after(0, self._finish)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _set_progress(self, done, total, stage):
        self.prog.config(maximum=max(total, 1))
        self.prog.config(value=done)
        self.status.config(text=f"{stage}: {done}/{total}")

    def _finish(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status.config(text="就绪")

    def _stop(self):
        self.stop_event.set()
        self.btn_stop.config(state="disabled")
        self.status.config(text="正在停止…")


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
