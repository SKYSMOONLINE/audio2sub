import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
import config
from core.transcribe import resolve_model_path
from web.jobs import manager
from web.orchestrator import run_job

DEFAULT_OUT = APP_DIR / "output"
DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="audio2sub")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


class JobRequest(BaseModel):
    audio_paths: list[str] = Field(default_factory=list)
    folder: str | None = None
    mode: str = "zh_clean"
    model: str = config.DEFAULT_MODEL
    lang: str = config.DEFAULT_LANG
    use_prompt: bool = True
    denoise: bool = True
    glossary: bool = True
    llm: str = config.DEFAULT_LLM
    out_dir: str | None = None


class SettingsRequest(BaseModel):
    deepseek_key: str = ""


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"ok": True, "gpu_hint": None}


@app.get("/api/bootstrap")
def bootstrap():
    models = []
    for label, key in config.MODEL_LABELS.items():
        try:
            available = Path(resolve_model_path(key)).is_dir()
        except Exception:
            available = False
        models.append({"key": key, "label": label, "available": available})
    return {
        "models": models,
        "languages": [{"key": value, "label": label} for label, value in config.LANG_LABELS.items()],
        "modes": [
            {"key": "zh_clean", "label": "精简中文"},
            {"key": "zh", "label": "纯中文"},
            {"key": "bilingual", "label": "中文（日文）"},
            {"key": "both", "label": "两份字幕"},
            {"key": "jp_raw", "label": "日语原文"},
        ],
        "llms": config.LLM_CHOICES,
        "has_key": bool(config.load_api_key()),
        "default_out_dir": str(DEFAULT_OUT),
        "jobs": manager.summaries(),
    }


@app.put("/api/settings")
def settings(body: SettingsRequest):
    config.save_api_key(body.deepseek_key.strip())
    return {"has_key": bool(body.deepseek_key.strip())}


def _dir_payload(raw_path):
    path = Path(raw_path).expanduser()
    if not path.exists() or not path.is_dir():
        raise HTTPException(404, "目录不存在或不可访问")
    dirs, audio = [], []
    try:
        for item in path.iterdir():
            if item.is_dir():
                dirs.append({"name": item.name, "path": str(item)})
            elif item.is_file() and item.suffix.lower() in config.AUDIO_EXTS:
                audio.append({"name": item.name, "path": str(item), "size": item.stat().st_size, "ext": item.suffix.lower()})
    except OSError as exc:
        raise HTTPException(404, str(exc))
    dirs.sort(key=lambda item: item["name"].casefold())
    audio.sort(key=lambda item: item["name"].casefold())
    return {"path": str(path), "parent": str(path.parent) if path.parent != path else None, "dirs": dirs, "audio": audio}


@app.get("/api/dirs/roots")
def roots():
    if os.name == "nt":
        values = [f"{chr(code)}:/" for code in range(ord("A"), ord("Z") + 1) if Path(f"{chr(code)}:/").exists()]
    else:
        values = ["/"]
    return {"roots": values}


@app.get("/api/dirs/list")
def list_dir(path: str = Query(...)):
    return _dir_payload(path)


@app.post("/api/jobs", status_code=201)
def create_job(body: JobRequest):
    if body.folder:
        audio_paths = [item["path"] for item in _dir_payload(body.folder)["audio"]]
    else:
        audio_paths = [str(Path(path)) for path in body.audio_paths]
    if not audio_paths:
        raise HTTPException(400, "必须选择音频文件或文件夹")
    missing = [path for path in audio_paths if not Path(path).is_file()]
    if missing:
        raise HTTPException(400, f"音频文件不存在: {missing[0]}")
    out_dir = str(Path(body.out_dir).expanduser()) if body.out_dir else str(DEFAULT_OUT)
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    payload.update(audio_paths=audio_paths, out_dir=out_dir)
    job = manager.create(payload, lambda current: run_job(current, manager))
    return {"job_id": job.id}


@app.get("/api/jobs")
def jobs():
    return manager.summaries()


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, after: int = 0):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")

    def stream():
        for event in manager.stream(job, after):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield ": close\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    job.stop_event.set()
    if job.state == "queued":
        manager.finish(job, "cancelled")
    return {"ok": True, "state": job.state}


@app.get("/api/download")
def download(path: str = Query(...)):
    file = Path(path)
    if not file.is_file() or file.suffix.lower() != ".lrc":
        raise HTTPException(404, "结果文件不存在")
    return FileResponse(str(file), filename=file.name, media_type="text/plain; charset=utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8710)
