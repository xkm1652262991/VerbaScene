import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from app.config import settings
from app.runner import WanRunner
from app.schemas import HealthResponse, JobCreate, JobList, JobRecord, RunMode
from app.store import JobStore, utcnow


store = JobStore(settings.data_root)
runner = WanRunner(store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = app.state.worker = __import__("asyncio").create_task(runner.worker())
    try:
        yield
    finally:
        worker.cancel()


def create_app() -> FastAPI:
    return FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )


app = create_app()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        model_dir_exists=settings.wan_ckpt_dir.exists(),
        repo_dir_exists=(settings.wan_repo_dir / "generate.py").exists(),
        queue_depth=runner.queue.qsize(),
        current_job_id=runner.current_job_id,
    )


@app.get("/v1/gpus")
async def get_gpus() -> dict[str, list[dict[str, str]]]:
    fields = [
        "index",
        "name",
        "utilization.gpu",
        "memory.used",
        "memory.total",
        "temperature.gpu",
        "power.draw",
    ]
    process = await asyncio.create_subprocess_exec(
        "nvidia-smi",
        f"--query-gpu={','.join(fields)}",
        "--format=csv,noheader,nounits",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise HTTPException(status_code=500, detail=stderr.decode("utf-8", errors="replace"))

    items: list[dict[str, str]] = []
    for line in stdout.decode("utf-8").splitlines():
        values = [part.strip() for part in line.split(",")]
        items.append(dict(zip(fields, values)))
    return {"items": items}


@app.post("/v1/i2v", response_model=JobRecord)
async def create_i2v_job(
    prompt: str = Form(...),
    image: UploadFile = File(...),
    size: str = Form(settings.default_size),
    mode: RunMode | None = Form(None),
    gpu_devices: str | None = Form(None),
    frame_num: int | None = Form(None),
    sample_steps: int | None = Form(None),
    seed: int | None = Form(None),
) -> JobRecord:
    request = JobCreate(
        prompt=prompt,
        size=size,
        mode=mode,
        gpu_devices=gpu_devices,
        frame_num=frame_num,
        sample_steps=sample_steps,
        seed=seed,
    )
    suffix = Path(image.filename or "input.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="image must be jpg, jpeg, png, or webp")

    job_id = uuid.uuid4().hex
    image_path = store.inputs_dir / f"{job_id}{suffix}"
    output_path = store.outputs_dir / f"{job_id}.mp4"
    log_path = store.logs_dir / f"{job_id}.log"

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="image is empty")
    image_path.write_bytes(content)

    now = utcnow()
    job = JobRecord(
        id=job_id,
        status="queued",
        prompt=request.prompt,
        size=request.size,
        mode=request.mode or settings.default_mode,
        gpu_devices=request.gpu_devices or settings.default_gpu_devices,
        image_path=str(image_path),
        output_path=str(output_path),
        log_path=str(log_path),
        created_at=now,
        updated_at=now,
        frame_num=request.frame_num,
        sample_steps=request.sample_steps,
        seed=request.seed,
    )
    store.save(job)
    await runner.enqueue(job_id)
    return job


@app.get("/v1/jobs", response_model=JobList)
async def list_jobs() -> JobList:
    items = store.list()
    return JobList(total=len(items), items=items)


@app.get("/v1/jobs/{job_id}", response_model=JobRecord)
async def get_job(job_id: str) -> JobRecord:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, bool]:
    ok = await runner.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job not found")
    return {"cancelled": True}


@app.get("/v1/jobs/{job_id}/log", response_class=PlainTextResponse)
async def get_log(job_id: str) -> str:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    path = Path(job.log_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


@app.get("/v1/jobs/{job_id}/video")
async def get_video(job_id: str) -> FileResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    path = Path(job.output_path)
    if job.status != "succeeded" or not path.exists():
        raise HTTPException(status_code=404, detail="video is not ready")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")
