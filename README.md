# audio2sub 🎙️

> **专为日语音声作品（ASMR / 广播剧 / DLsite RJ 编号作品）打造的本地 GPU 高精度声学对齐与自进化字幕工作流。**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Faster-Whisper](https://img.shields.io/badge/ASR-Faster--Whisper-orange.svg)](https://github.com/SYSTRAN/faster-whisper)
[![CUDA Acceleration](https://img.shields.io/badge/CUDA-int8__float16-green.svg)](https://developer.nvidia.com/cuda-toolkit)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-36%20passed-success.svg)](tests/)

---

## 💡 为什么需要 audio2sub？

常规通用语音识别工具（如默认参数的 Whisper）在处理日语音声与 ASMR 作品时，经常出现各种“翻车”：
1. **时间轴严重漂移**：默认 30 秒分块切片在 ASMR 的大段长静音或舔耳水声中，会导致时间轴强行吸附到整十秒，产生 **8~15 秒的严重滞后**；
2. **提示词泄漏与复读**：简单粗暴地在提示词里堆叠专有名词（如 `語彙：A、B、C`），会导致模型在弱音空白区把提示词当台词吐出，或陷入无休止复读；
3. **近麦喷气与低频轰鸣**：贴麦录音中强烈的呼吸气流干扰起音判定；
4. **人设称谓崩坏**：机翻常把角色称呼听众的「おじさん」机械直译为“老爷爷 / 老爷子 / 小老头”，彻底摧毁代入感；
5. **经验不可复用**：修改了某个专有名词或空耳，换一部作品又得重新手动修一遍。

`audio2sub` 彻底解决了上述问题，提供了一套**100% 本地离线、高精度声学对齐、具备自愈质检与自学习能力**的完整工程方案。

---

## 🌟 核心特性

- 🎯 **毫秒级 DTW 声学对齐**：开启 `word_timestamps=True`，强制提取每句台词首词的真实声学物理起音点（`s.words[0].start`），杜绝时间轴漂移。
- 🌊 **80Hz FIR 声学高通滤波**：纯 NumPy 编写的 65 阶汉明窗高通滤波，消除贴麦录音中 <80Hz 的气流喷麦与低频轰鸣，配合温和自适应动态增益，防止气声耳语漏识。
- 🎭 **9 类微剧场情境 Prompt（Micro-theatre）**：内置漫咖隔间、NTR出轨、雌小鬼辣妹、催眠洗脑、纯爱姐弟、调教主奴、保健室、露天温泉、直播网配等场景化台词框架，严格压制在 135 Token 内，杜绝提示词回响。
- 🛡️ **四级字幕质检门禁（Sanity Gate）**：
  1. **行对称性核验**：严格校验日文与中文 1:1 行对齐；
  2. **开场回响清洗**：自动剔除前 6 秒静音区的提示词泄漏片段；
  3. **称谓智能自愈**：将“老爷爷 / 老爷子 / 小老头 / 老头子”自动治愈为亲昵自然的“大叔”；
  4. **标点空行清理**：纯符号及空白行对称剔除。
- 🔄 **反哺自进化引擎（Feedback Loop）**：每次听译完成后，通过命令行即可学习新词汇、伪影过滤规则与声学纠偏短语。**内置安全门禁，严禁危险的单字裸词替换**，防止误伤正常词汇，且每次入库均自动生成带时间戳的安全备份（`.backups/`）。
- 🧩 **Agent 原生解耦协议**：支持 Antigravity、Gemini、Claude 等大模型或人工翻译通过标准 `translation_request.json` 工单协议无缝交接。

---

## 📊 硬件要求与资源占用

| 维度 | 指标 | 说明 |
| :--- | :--- | :--- |
| **显存 (VRAM)** | **约 3.5 GB ~ 4.2 GB** | 采用 `int8_float16` 精度量化。主流 6GB 显存的 RTX 3060 / 4060 笔记本及台式机即可极速流畅运行。 |
| **内存 (RAM)** | **约 2.0 GB ~ 3.5 GB** | 极低内存占用。 |
| **处理速度** | 视 GPU 性能而定 | 在 RTX 3060 上，一段 15 分钟的音轨通常仅需 1~2 分钟即可完成全套声学转录与对齐。 |
| **CPU 回退** | 支持 | 未检测到 CUDA 显卡时自动安全回退至 CPU（int8）模式。 |

---

## 🚀 快速部署指南

### 1. 克隆仓库与创建虚拟环境
推荐使用 **Python 3.10 ~ 3.12**：
```bash
git clone https://github.com/SKYSMOONLINE/audio2sub.git
cd audio2sub

python -m venv .venv
# Windows PowerShell 激活环境:
.venv\Scripts\Activate.ps1
```

### 2. 安装核心依赖
```bash
pip install -r requirements.txt
pip install -e .
```

### 3. Windows cuBLAS 动态库支持（关键）
为避免 Faster-Whisper 在 Windows 下报 `cublas64_*.dll not found`，请安装官方显卡加速支持包：
```bash
pip install nvidia-cublas-cu12
```
*注：代码内置了自动路径嗅探，会自动将 Python 环境中的 nvidia bin 目录加入系统 `PATH`。*

### 4. 准备离线模型
下载 CTranslate2 格式的 `Systran/faster-whisper-large-v2` 模型文件（含 `model.bin`, `config.json`, `tokenizer.json`），放置到：
`models/large-v2`
*(或通过系统环境变量 `WHISPER_MODEL_LARGE_V2` 自定义绝对路径)*

### 5. 环境诊断
运行内置诊断指令，所有项输出 `True` 即表示部署成功：
```bash
python -c "from core.diagnostics import diagnose_environment; print(diagnose_environment())"
```

---

## 💻 使用方法

### 1. 全专一键自动化批处理
指定音声作品所在的文件夹（例如包含音频文件和 `readme.txt` 的 RJ 目录）：
```bash
python run_asmr.py "D:\yinsheng\RJ01234567"
```
*(可追加 `--genre` 参数指定预设题材，如 `manga_cafe`, `mesugaki_gyaru`, `pure_love_childhood` 等)*

**执行过程将自动完成：**
1. **元数据嗅探**：扫描目录内的文档，识别 RJ 编号、作品名、声优（CV）、登场角色与题材标签；
2. **显存常驻复用**：模型单例常驻，音轨间切换零冷启动；
3. **逐轨导出基准**：在音频同目录下输出：
   - `[Track].日文原稿.lrc`（带精准时间轴的日文字幕）
   - `[Track].日文原稿.txt`（纯台词原稿）
   - `[Track].segments.json`（包含起止毫秒与元数据的结构化数据）

### 2. 导出播放器专用的纯中文 LRC
当你在音频目录下准备好 1:1 逐行翻译的 `[Track].中文翻译.txt` 时：
再次运行脚本，系统会自动触发**四级质检门禁**：
```text
✔ [质检门禁] 45/45 句对齐 | 自愈修正: 1处称呼 | 剔除回响: 0句
✔ [主字幕] 已通过质检门禁导出纯中文 LRC: [Track].lrc
```
最终生成的 `[Track].lrc` 为标准 `[mm:ss.xx]中文` 格式，各类播放器（PotPlayer、手机音乐播放器等）均可即开即显。

---

## 🔄 自进化反哺学习引擎

遇到未收录的新黑话、角色名或声学空耳，通过命令行即可直接教给系统：

### 1. 学习新专有词汇 / 俚语
```bash
python core/feedback_loop.py learn-word "メスガキ搾精" --category "actions_and_sex" --note "新作常见词"
```
*(内置安全门禁：严禁将常见高频日本姓氏加入全局人设库，防止污染通用模型)*

### 2. 学习声学纠偏规则（强制短语上下文绑定）
比如声优把“大叔”读得模糊被模型听成“味噌”：
```bash
python core/feedback_loop.py learn-fix "お味噌の(ちんぽ|精液)" "おじさんの\1" --note "大叔在特定生殖语境下的误听纠偏"
```
> [!IMPORTANT]
> **安全门禁阻断**：若尝试学习孤立裸词（如单独的 `お味噌`），系统将自动报错拦截并拒绝写入，保护现有日常词汇不被误伤。

### 3. 学习过滤片尾套话与复读伪影
```bash
python core/feedback_loop.py learn-hallucination "ご視聴ありがとうございました" --note "常见片尾伪影"
```

所有反哺学习均会在 `references/.backups/` 下自动创建时间戳备份，支持随时零风险回滚。

---

## 🧪 测试与质量保证

项目包含完整的单元测试与时序鲁棒性校验，运行：
```bash
python -m unittest discover -s tests -v
```
36 项工程级测试覆盖时序边界、字幕渲染、协议交接与异常隔离，测试耗时约 1.8 秒全绿通过。

---

## 📄 开源许可证

本项目基于 [MIT 许可证](LICENSE) 开源。欢迎提交 Issue 与 Pull Request！
