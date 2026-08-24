from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


BASE = Path(os.environ.get("WAN_OFFICIAL_BASE", "/opt/verbascene/Wan2.2-official"))
CKPT_DIR = Path(os.environ.get("WAN_OFFICIAL_CKPT", "/opt/verbascene/models/Wan2.2-I2V-A14B"))
JOBS_DIR = Path(os.environ.get("WAN_OFFICIAL_JOBS", "/var/lib/verbascene/wan-official/jobs"))
OUTPUTS_DIR = Path(os.environ.get("WAN_OFFICIAL_OUTPUTS", "/var/lib/verbascene/wan-official/outputs"))
PUBLIC_BASE_URL = os.environ.get("WAN_OFFICIAL_PUBLIC_BASE_URL", "http://127.0.0.1:18083").rstrip("/")
API_KEY = os.environ.get("WAN_OFFICIAL_API_KEY", "")
DEVICES = os.environ.get("ASCEND_RT_VISIBLE_DEVICES", "0,1,2,3,4,5,6,7")

JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Wan2.2 Official I2V API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type"],
)

queue: asyncio.Queue[str] = asyncio.Queue()
active_job_id: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_auth(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    if x_api_key == API_KEY or bearer == API_KEY:
        return
    raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing or invalid API key"})


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def job_json(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def load_job(job_id: str) -> dict[str, Any]:
    path = job_json(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "Job not found"})
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(job: dict[str, Any]) -> None:
    path = job_json(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def read_log_tail(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def model_state() -> dict[str, Any]:
    size = dir_size(CKPT_DIR)
    return {
        "path": str(CKPT_DIR),
        "exists": CKPT_DIR.exists(),
        "size_bytes": size,
        "size_gb": round(size / 1024 / 1024 / 1024, 2),
        "ready": (CKPT_DIR / "Wan2.1_VAE.pth").exists() and (CKPT_DIR / "models_t5_umt5-xxl-enc-bf16.pth").exists(),
    }


def public_job(job: dict[str, Any], include_log: bool = False) -> dict[str, Any]:
    data = dict(job)
    data.pop("local_paths", None)
    data["status_url"] = f"{PUBLIC_BASE_URL}/v1/i2v/jobs/{job['id']}"
    if job.get("outputs", {}).get("native"):
        data["result_url"] = f"{PUBLIC_BASE_URL}/v1/i2v/jobs/{job['id']}/result?variant=native"
    if include_log:
        data["log_tail"] = read_log_tail(Path(job["local_paths"]["log"]))
    return {"data": data}


def validate(frames: int, steps: int, size: str) -> None:
    if frames < 1 or frames > 161:
        raise HTTPException(status_code=422, detail={"code": "invalid_frames", "message": "frames must be 1..161"})
    if steps < 1 or steps > 80:
        raise HTTPException(status_code=422, detail={"code": "invalid_steps", "message": "steps must be 1..80"})
    if size not in {"1280*720", "720*1280"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_size", "message": "size must be 1280*720 or 720*1280"})


async def run_worker() -> None:
    global active_job_id
    while True:
        job_id = await queue.get()
        active_job_id = job_id
        try:
            await run_job(job_id)
        finally:
            active_job_id = None
            queue.task_done()


async def run_job(job_id: str) -> None:
    job = load_job(job_id)
    if not model_state()["ready"]:
        job.update({"status": "failed", "error": {"code": "model_not_ready"}, "finished_at": utc_now(), "updated_at": utc_now()})
        save_job(job)
        return

    job.update({"status": "running", "started_at": utc_now(), "updated_at": utc_now()})
    save_job(job)

    local = job["local_paths"]
    request = job["request"]
    log_path = Path(local["log"])
    out_path = Path(local["output"])
    cmd = [
        "/opt/wan2-i2v-a14b-diffusers/conda/bin/torchrun",
        "--nproc_per_node=8",
        "generate.py",
        "--task",
        "i2v-A14B",
        "--size",
        request["size"],
        "--frame_num",
        str(request["frames"]),
        "--ckpt_dir",
        str(CKPT_DIR),
        "--t5_fsdp",
        "--dit_fsdp",
        "--ulysses_size",
        "8",
        "--save_file",
        str(out_path),
        "--prompt",
        request["prompt"],
        "--image",
        local["image"],
        "--sample_steps",
        str(request["steps"]),
        "--base_seed",
        str(request["seed"]),
    ]

    env = dict(os.environ)
    env["ASCEND_RT_VISIBLE_DEVICES"] = DEVICES
    env["PYTHONUNBUFFERED"] = "1"
    env["PATH"] = "/opt/wan2-i2v-a14b-diffusers/conda/bin:" + env.get("PATH", "")

    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"started_at={utc_now()}\ncmd={' '.join(cmd)}\n")
        log.flush()
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(BASE), env=env, stdout=log, stderr=subprocess.STDOUT)
        rc = await proc.wait()

    job = load_job(job_id)
    if rc == 0 and out_path.exists():
        job.update({"status": "succeeded", "outputs": {"native": out_path.name}, "error": None, "finished_at": utc_now(), "updated_at": utc_now()})
    else:
        job.update({"status": "failed", "error": {"code": "generation_failed", "message": f"torchrun exited with code {rc}", "log_tail": read_log_tail(log_path)}, "finished_at": utc_now(), "updated_at": utc_now()})
    save_job(job)


@app.on_event("startup")
async def startup() -> None:
    for path in JOBS_DIR.glob("*/job.json"):
        job = json.loads(path.read_text(encoding="utf-8"))
        if job.get("status") in {"queued", "running"}:
            job.update({"status": "failed", "error": {"code": "service_restarted"}, "finished_at": utc_now(), "updated_at": utc_now()})
            save_job(job)
    asyncio.create_task(run_worker())


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"data": {"ok": True, "active_job_id": active_job_id, "queue_size": queue.qsize(), "devices": DEVICES, "model": model_state()}}


@app.post("/v1/i2v/jobs", status_code=201)
async def create_job(
    _: None = Depends(require_auth),
    image: UploadFile | None = File(default=None),
    image_url: str | None = Form(default=None),
    prompt: str = Form(...),
    frames: int = Form(default=81),
    steps: int = Form(default=40),
    size: str = Form(default="1280*720"),
    seed: int = Form(default=0),
    client_job_id: str | None = Form(default=None),
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail={"code": "empty_prompt", "message": "prompt is required"})
    validate(frames, steps, size)
    if image is None and not image_url:
        raise HTTPException(status_code=422, detail={"code": "missing_image", "message": "image or image_url is required"})
    if image is not None and image_url:
        raise HTTPException(status_code=422, detail={"code": "ambiguous_image", "message": "send either image or image_url"})

    job_id = client_job_id or uuid.uuid4().hex
    root = job_dir(job_id)
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "input_image"
    if image is not None:
        suffix = Path(image.filename or "").suffix or ".png"
        image_path = image_path.with_suffix(suffix)
        with image_path.open("wb") as f:
            shutil.copyfileobj(image.file, f)
    else:
        parsed = urlparse(image_url or "")
        if parsed.scheme not in {"http", "https"}:
            raise HTTPException(status_code=422, detail={"code": "unsupported_image_url", "message": "image_url must use http or https"})
        req = UrlRequest(image_url or "", headers={"User-Agent": "wan-official-api/0.1"})
        with urlopen(req, timeout=60) as resp, image_path.with_suffix(".jpg").open("wb") as f:
            image_path = image_path.with_suffix(".jpg")
            shutil.copyfileobj(resp, f)

    output_path = OUTPUTS_DIR / f"{job_id}.mp4"
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "request": {"prompt": prompt, "frames": frames, "steps": steps, "size": size, "seed": seed},
        "outputs": {},
        "error": None,
        "local_paths": {"image": str(image_path), "output": str(output_path), "log": str(root / "run.log")},
    }
    save_job(job)
    await queue.put(job_id)
    return public_job(job)


@app.get("/v1/i2v/jobs")
async def list_jobs(_: None = Depends(require_auth), limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    jobs = []
    for path in sorted(JOBS_DIR.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        jobs.append(public_job(json.loads(path.read_text(encoding="utf-8")))["data"])
    return {"data": jobs}


@app.get("/v1/i2v/jobs/{job_id}")
async def get_job(job_id: str, _: None = Depends(require_auth), include_log: bool = Query(default=False)) -> dict[str, Any]:
    return public_job(load_job(job_id), include_log=include_log)


@app.get("/v1/i2v/jobs/{job_id}/result")
async def get_result(job_id: str, _: None = Depends(require_auth), variant: str = Query(default="native")) -> FileResponse:
    job = load_job(job_id)
    if job.get("status") != "succeeded":
        raise HTTPException(status_code=409, detail={"code": "job_not_succeeded", "status": job.get("status")})
    path = Path(job["local_paths"]["output"])
    if not path.exists():
        raise HTTPException(status_code=404, detail={"code": "result_missing"})
    return FileResponse(path, media_type="video/mp4", filename=path.name)
