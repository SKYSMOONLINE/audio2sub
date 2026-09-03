# audio2sub Web UI — 业务逻辑规格（后端）

> 目标：把现有 Tkinter GUI（`D:\ASMR\audio2sub\app.py`）重做成一个 **本机 Web 应用**（127.0.0.1），前端为 Linear 风格产品界面（由 code agent 按 `linear-style-ui` skill 实现）。
> 本文件只描述**业务逻辑/后端规格**，供 code agent 实现；前端视觉归 `linear-style-ui` skill 管。
> 现状基线：GUI + core 流水线已全部验证通过（`#8` 端到端 OK），**core 的翻译/去噪/术语统一/精简逻辑是审定过的资产，不得重写其判定逻辑与提示词**。

---

## 1. 现状与复用边界

现有可复用模块（路径均相对 `D:\ASMR\audio2sub\`）：

| 文件 | 职责 | Web 中的角色 |
|---|---|---|
| `config.py` | 模型注册表、语言/LLM 选项、`HF_ENDPOINT`、**cuBLAS DLL PATH 注入**、`.api_key.json` 读写 | 直接复用（**必须先于 faster-whisper 加载被 import**，与 `core/transcribe.py` 现状一致） |
| `core/transcribe.py` | `load_model`（CUDA→CPU 自动回退）、`transcribe_audio` | 直接复用 |
| `core/postprocess.py` | `merge_segments`、`clean_fillers_cn` | 直接复用 |
| `core/translate.py` | `translate_lines`、`denoise_lines`、`glossary_pass`（含三套中文提示词） | 直接复用 |
| `core/subtitles.py` | `fmt_ts`、`save_lrc` | 直接复用 |
| `app.py` | Tkinter GUI + `pipeline()` 编排（阶段顺序、按 mode 组装输出行） | **编排逻辑参照它重写为 `web/orchestrator.py`；GUI 文件保留不破坏** |
| `domain_prompt.txt` | Whisper initial_prompt 领域词表 | 直接复用 |
| `.api_key.json` | DeepSeek key（已恢复） | 后端读写，**绝不回传前端** |

流水线（GUI 已定版，web 必须逐阶段一致）：
`音频 → Whisper 听译(带领域提示词) → merge_segments 断句 → denoise_lines 噪音行清洗 → translate_lines 逐句翻译 → glossary_pass 术语统一 → clean_fillers_cn/按模式组行 → save_lrc 写出`

允许对 core 的**最小加法**（签名兼容、不改提示词/判定逻辑）：
- 给 `translate_lines` / `denoise_lines` / `glossary_pass` 增加可选 `stop_event=None` 参数，循环内每句前 `if stop_event and stop_event.is_set(): break/return`，用于取消。
- 其它一律不改。

## 2. 架构

```
浏览器 (Linear 风格 SPA)
   │  REST + SSE (http://127.0.0.1:8710)
   ▼
FastAPI (web/server.py) ── 静态资源: web/static/  ── 输出目录下载: /api/download
   │
   ├─ JobsManager (web/jobs.py)：Job 状态机 + 日志环形缓冲 + SSE 事件分发；全局单写锁
   │     后台线程跑 orchestrator.run_job(job)；同一时刻最多 1 个任务在跑，其余排队
   │
   └─ orchestrator.py（run_job）：复用 core.*；向 Job 上报 log / progress / cancel
```

- 单机单用户、仅监听 `127.0.0.1`，不做鉴权。
- **Whisper 模型进程级单例**：首个任务懒加载并缓存（job.stats.device 记录 cuda/cpu），后续任务直接复用，避免反复加载。
- 任务串行执行（GPU/显存约束）；排队状态需在 UI 明示。
- 无 ffmpeg 依赖（此前已确认用不到），音频时长不探测，转写进度为粗粒度提示即可。

## 3. Job 数据模型

请求体 `POST /api/jobs`：

```jsonc
{
  "audio_paths": ["D:/yinsheng/RJ01560861/本編/#8.電話しながら絶頂生セ.wav"], // 单文件/多选
  "folder": null,                 // 或目录模式：服务端扫该目录下 AUDIO_EXTS（非递归、排序同 GUI）
  "mode": "zh_clean",             // zh_clean|zh|bilingual|both|jp_raw
  "model": "large-v2",            // large-v2|small|tiny（不可用项前端禁用）
  "lang": "ja",
  "use_prompt": true,
  "denoise": true,                // 与 GUI 默认一致
  "glossary": true,               // 与 GUI 默认一致
  "llm": "deepseek-v4-flash",
  "out_dir": null                 // null → 默认 D:\ASMR\audio2sub\output
}
```

Job 对象（含状态/进度/日志/结果）：

```jsonc
{
  "id": "j_8f3a…",
  "state": "queued|running|done|failed|cancelled",
  "stage": "idle|load_model|transcribe|merge|denoise|translate|glossary|write",
  "stage_label": "翻译 23/71",        // 前端直接展示
  "files_total": 1, "file_index": 1, "file_name": "#8…wav",
  "progress": {"done": 23, "total": 71},
  "logs": [{"t": 1725…, "msg": "  清洗噪音行：77 -> 71 句", "level": "info|warn|error"}],  // 环形上限 2000，含尾部指针便于续传
  "results": [{"audio": "#8….wav", "lrcs": [{"name": "#8….lrc", "lines": 68, "path": "D:/ASMR/audio2sub/output/#8….lrc"}]}],
  "error": null,
  "stats": {"device": "cuda|int8", "lang_detected": "ja", "segments": 143, "sentences": 71,
            "removed_noise": 6, "translated_ok": 71, "elapsed_s": 894},
  "created_at": 1725…, "started_at": null, "finished_at": null
}
```

API key 规则同 GUI：需要翻译/去噪/术语统一的任务必须已有保存的 key（`config.load_api_key()`），否则任务以明确错误失败（`failed` + error="需要 DeepSeek API Key…"）；`jp_raw` 模式免 key。

## 4. REST + SSE API

| 方法/路径 | 说明 |
|---|---|
| `GET /api/health` | `{ok, gpu_hint}`（gpu_hint 仅首次加载后可信） |
| `GET /api/bootstrap` | 一次性拉取：模型列表+可用性（本地路径是否存在）、语言/模式/LLM 选项、`has_key`、默认 out_dir、任务列表摘要 |
| `PUT /api/settings` | `{deepseek_key}` → `config.save_api_key`；返回 `{has_key}`，**不回传 key 明文** |
| `GET /api/dirs/roots` | 盘符列表（Windows: `D:/` 等） |
| `GET /api/dirs/list?path=D:/yinsheng/RJ01560861/本編` | `{path, parent, dirs:[…], audio:[{name,path,size,ext}]}`；音频仅 AUDIO_EXTS；目录优先再文件名排序；不存在/无权访问返回 404+msg |
| `POST /api/jobs` | 校验：必须有 audio_paths 或 folder；音频文件须存在。返回 `201 {job_id}`；已有任务在跑则入队 |
| `GET /api/jobs` | 摘要列表（倒序，上限 50） |
| `GET /api/jobs/{id}` | 完整 Job（logs 截取最近 500 条防抖） |
| `GET /api/jobs/{id}/events?after=N` | **SSE**：事件流，见下 |
| `POST /api/jobs/{id}/cancel` | 置取消标记（协作式）；幂等 |
| `GET /api/download?path=<绝对路径>` | 任意已产出 LRC 文件下载（`Content-Disposition: attachment`；仅本机可信场景） |

### SSE 事件（text/event-stream）
事件体统一 JSON，每条带递增 `seq`：
- `{type:"state", state:"running", seq}`
- `{type:"stage", stage:"translate", label:"翻译 23/71", progress:{done,total}, seq}`
- `{type:"log", msg, level, seq}`（逐条推送；`after=N` 重连时先补发 `seq>N` 的缓冲事件再转实时）
- `{type:"done"|"failed"|"cancelled", seq}`（终态后服务端保持连接 5s 供客户端收尾，再关闭；EventSource 自动重连时 `after` 兜底）
- 心跳：每 15s `: ping`

### 进度/阶段映射（对齐真实耗时）
| stage | 计数 | 耗时量级（#8 实测） |
|---|---|---|
| load_model | 无（spinner + stage_label“加载模型…”） | 8–10s（已缓存后近 0） |
| transcribe | file_index/files_total + stage_label 文件名 | 60–130s/文件 |
| merge | 无 | 瞬时 |
| denoise | 无（stage_label“清洗噪音行…”，日志报 N->M） | 30–60s |
| translate | done=已译句数, total=句数 | **主体，60–900s**，每句打点 |
| glossary | 无（label“术语统一…”，≤150 句/次调用） | 10–60s |
| write | file_index/files_total | 瞬时 |

取消：检查点在「每句翻译前 / 每去噪块间 / 术语统一块间 / 每个音频文件前」；取消后 job 转 `cancelled`，已写文件保留。

## 5. 输出与命名（与 GUI 一致）

- 默认 `out_dir`：`D:\ASMR\audio2sub\output`（启动自动建目录）。
- 每音频按 mode 产出（GUI 同名逻辑，**建议抽到 `core/subtitles.py` 供 GUI/web 共用，抽完须保证 GUI 行为不变**）：
  - `zh_clean` → `名称.lrc`（走 `clean_fillers_cn`，纯语气行被删）
  - `zh` → `名称.lrc`；`bilingual` → `名称.lrc`；`jp_raw` → `名称.lrc`
  - `both` → 先写精简 `名称.lrc`，再写 `名称.双语.lrc`
- 重名去重：`名称_1.lrc`、`_2`…（与 GUI `_write` 相同）。
- 每个 lrc 用 `save_lrc`（已有行号/时间戳格式）。

## 6. 前端功能清单（供 linear-style-ui skill 组合，UI 文案用简体中文）

1. **来源选择**：切换「单个文件 / 整个文件夹」→ 打开**目录浏览弹层**（盘符→逐级目录→点选音频多选/选文件夹）。来源区顶部显示待处理清单（名称+大小）。
2. **任务配置**：模型、语言、三个开关（领域提示词 / 清除噪音行 / 术语统一）、输出模式（zh_clean 为默认推荐）、输出目录（默认 output，可改或清空回默认）、主按钮「开始处理」。
3. **设置**：DeepSeek API Key 输入（掩码）+ 保存（仅提示“已保存/有效”，不回显 key）。
4. **任务列表**：每行：文件名、状态 pill（排队/处理中/完成/失败/已取消）、耗时、展开显示日志（等宽字体滚动）+ 阶段进度条（翻译时 done/total）+ 结果文件（下载按钮）。日志随 SSE 实时追加；终态自动滚动到底。
5. **交互**：Keyboard（Enter 触发开始、Esc 关闭弹层、方向键浏览目录）；处理中禁用相关控件并显示不可重复提交；空状态/错误状态齐全；控制台不得报 JS 错误。
6. **语言/字符**：中文文案；文件名为日文原样显示不转义错乱（UTF-8）。

## 7. 运行方式

```bat
:: 一次性装依赖（在 D:\ASMR\openlrc\.venv 里）
D:\ASMR\openlrc\.venv\Scripts\python.exe -m pip install fastapi uvicorn

:: 启动（start_web.bat 内容）—— 端口 8710，仅 127.0.0.1
cd /d D:\ASMR\audio2sub
start "" http://127.0.0.1:8710
D:\ASMR\openlrc\.venv\Scripts\python.exe -m web.server
```

## 8. 边界与异常

- 已知非致命告警 `Could not load preprocessor config … (char 0)`：照常忽略。
- 模型路径不存在 → 前端对应模型禁用；请求仍选到则 `failed` 并给出清晰错误。
- 无 API key 且 mode≠jp_raw → `failed`，error 提示（同 GUI）。
- GPU 不可用自动回退 CPU int8：设备为 cpu 时转写显著变慢，UI 在 stats 或日志提示一次。
- 模型加载失败/显存不足 → `failed` + 错误文本；服务进程不退出。
- 同一音频被多任务同时写同一 out_dir：任务串行故天然避免。
- 服务重启后任务历史不要求持久化（内存即可，进程内可查）。
- 前端禁止将 key 传入任何任务请求体（key 只走 `PUT /api/settings`）。

## 9. 验收标准（后端部分）

1. `POST /api/jobs` 跑 `D:\yinsheng\RJ01560861\本編\#8.電話しながら絶頂生セ.wav`（zh_clean、默认开关全开、输出到临时目录），全流程成功：
   - 日志出现「清洗噪音行：N -> M」「翻译完成，M/M 句成功」「术语统一完成」；
   - 产出 LRC 通篇称呼一致（大叔），开头无乱码噪音行，无 `||`、无日文泄漏、无行首数字伪影；
2. `events` SSE 按序推送 state/stage/log/终态，`after` 重连不丢事件。
3. cancel 在翻译阶段生效（数秒内转 cancelled）。
4. 目录浏览：能列出 `D:\yinsheng\RJ01560861\本編` 的 .wav 并进入任务。
5. 无 key 时任务明确 failed；`jp_raw` 免 key 可用。
6. `app.py` GUI 行为不变（start.bat 双击仍可用）。

## 10. 参考文件
- 阶段/输出逻辑现成参考：`app.py::pipeline()`（读懂后照搬顺序，勿照抄 Tk 代码）。
- 选项定义：`config.py`（MODEL_LABELS / LANG_LABELS / LLM_CHOICES / AUDIO_EXTS / DEFAULT_*）。
- 领域词表：`domain_prompt.txt`。
