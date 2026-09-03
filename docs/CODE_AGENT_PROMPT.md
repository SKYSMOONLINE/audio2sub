# 任务：给 code agent 的提示词（复制整段使用）

你是资深全栈工程师，把一个**本机 Tkinter 工具改造成 Linear 风格的 Web 应用**。先读下面两个文件再动手，务必**按顺序执行**：

- 业务规格：`D:\ASMR\audio2sub\docs\WEB_BACKEND_SPEC.md`
- 现有 GUI 编排（阶段顺序、mode 输出逻辑的参照）：`D:\ASMR\audio2sub\app.py`
- 复用的审定模块：`D:\ASMR\audio2sub\core\`（transcribe / postprocess / translate / subtitles）、`D:\ASMR\audio2sub\config.py`

## 一、先做前端设计准备（Linear）

1. 通过 Skill 工具加载 **`linear-style-ui`** 技能；按其要求读取它 `references/` 下的来源与实现清单文件。
2. 探查仓库：这是纯 Python + 本地 venv 的 Windows 项目，**仓库里没有 Node/前端构建链**。优先采用**免构建的静态 SPA**（`web/static/index.html` + `app.css` + `app.js`，原生语义 HTML/CSS/JS，由 FastAPI 静态托管），不要引入 Node 工具链；除非你判断必须用框架，否则不要引入。
3. 写 UI 代码前，先输出一段「Linear alignment decisions」块（背景层级 / 密度 / 导航 / 强调色位置 / 边界 / 交互 / 响应式），只陈述本应用决策。
4. 中文产品文案（简体中文），界面是**本地工具工作台**不是营销页。

## 二、后端实现（FastAPI）

- 目录：新增 `web/`（`server.py`、`jobs.py`、`orchestrator.py`、`__init__.py`、`static/`）；加 `start_web.bat`；默认输出目录 `output/`。
- 依赖只允许新增 `fastapi`、`uvicorn`（装进 `D:\ASMR\openlrc\.venv`）。
- 按 `WEB_BACKEND_SPEC.md` 实现 REST + SSE、JobsManager（串行队列、Job 状态机、日志环形缓冲、SSE 事件带 seq + after 续传 + 心跳）、目录浏览、下载、设置接口、`orchestrator.run_job`。
- `orchestrator.py` 的流水线顺序与 `app.py::pipeline()` **逐阶段一致**，但**复用 `core.*` 的函数**；严禁改动 core 里已有的翻译提示词、去噪判定、术语统一提示词、精简规则。只允许规格里列出的「可选 stop_event 参数」这一种 core 加法（改完保证 GUI 仍可用）。
- mode→输出行/后缀的组装逻辑（zh_clean/zh/bilingual/both/jp_raw + `双语` 后缀 + 重名 `_n`）尽量抽到 `core/subtitles.py` 让 GUI 与 web 共用；若抽，`start.bat` 双击 GUI 必须仍正常。
- 音频来源用**服务端目录浏览**（音频本来就在这台机器的磁盘上），不做大文件上传。
- API key 只经 `PUT /api/settings` 保存到现有 `.api_key.json`；任务请求体里不许出现 key；任何接口不回传 key 明文。
- 仅监听 `127.0.0.1:8710`。Whisper 模型懒加载进程级单例、任务串行。

## 三、前端实现（Linear 风格）

按 `WEB_BACKEND_SPEC.md` §6 的功能清单做，并落实 linear-style-ui 的约束：
- 左侧导航/主工作台/任务列表的层级与密度对齐；一处强调色只用于“开始处理”等主操作与选中态。
- 状态（排队/处理中/完成/失败/取消）用克制的状态色与明确的文案/图标，避免整屏彩色。
- 完整交互态：hover/pressed/focus-visible/disabled/loading/空/错误；键盘（Enter 开始、Esc 关弹层、↑↓ 浏览目录）；遵守 `prefers-reduced-motion`。
- 日志区实时追加（SSE），等宽字体；任务展开时自动跟随滚动到底。
- 文件名为日文原样显示；全程 UTF-8；浏览器控制台零报错。

## 四、验证（必须做）

1. `GET /api/health`、`GET /api/bootstrap`、目录浏览接口 curl 一遍。
2. 用 curl 或页面提交一个真实任务：音频 `D:\yinsheng\RJ01560861\本編\#8.電話しながら絶頂生セ.wav`，`mode=zh_clean`，默认开关全开，`out_dir` 用临时目录。任务完成后核对 §9（规格）验收点：日志含「清洗噪音行 / 翻译完成 M/M / 术语统一完成」；LRC 称呼统一（大叔）、开头无乱码、无 `||`/日文泄漏/行首数字伪影。
3. 页面里跑一遍完整交互（选目录→选文件→配置→开始→日志滚动→结果下载），并测一次翻译阶段取消。
4. 最后 `D:\ASMR\audio2sub\start.bat` 双击确认原 GUI 仍可用。

## 五、交付物

1. `web/`（server+jobs+orchestrator+static 前端）+ `start_web.bat` + 规格要求的 core 最小改动（若有）。
2. 一段简短说明：端口/启动方式、你做的取舍、未验证项。

## 红线
- **不重写 core 的翻译/去噪/术语统一/精简逻辑与提示词**（已定版验证过）。
- **不破坏 GUI**（app.py 行为不变）。
- 不引入 ffmpeg / Node 构建链 / 额外大型依赖；不做鉴权与多用户。
- UI 是工具工作台，不是宣传页；风格 Linear 精神为主，不复制 Linear 的 logo/资产/逐像素截图。
