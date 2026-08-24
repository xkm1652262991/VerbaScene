#!/usr/bin/env python3
from __future__ import annotations

import gc
import io
import json
import logging
import os
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from PIL import Image

from wan.configs import MAX_AREA_CONFIGS, SUPPORTED_SIZES, WAN_CONFIGS
from wan.image2video import WanI2V
from wan.modules.model import WanModel
from wan.modules.t5 import T5EncoderModel
from wan.modules.vae2_1 import Wan2_1_VAE
from wan.utils.utils import save_video


logging.basicConfig(
    level=os.environ.get("WAN_I2V_LOG_LEVEL", "INFO").upper(),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("wan22-i2v-provider")

MODEL_DIR = Path(
    os.environ.get(
        "WAN_I2V_MODEL_DIR",
        "/opt/verbascene/models/Wan2.2-I2V-A14B",
    )
)
DATA_DIR = Path(os.environ.get("WAN_I2V_DATA_DIR", "/var/lib/verbascene/wan22-i2v"))
JOBS_DIR = DATA_DIR / "jobs"
INPUTS_DIR = DATA_DIR / "inputs"
OUTPUTS_DIR = DATA_DIR / "outputs"
API_KEY = os.environ.get("WAN_I2V_API_KEY", "")
MAX_QUEUE = int(os.environ.get("WAN_I2V_MAX_QUEUE", "4"))
MAX_UPLOAD_BYTES = int(os.environ.get("WAN_I2V_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
GPU_DEVICE = int(os.environ.get("WAN_I2V_GPU_DEVICE", "0"))
SUPPORTED_I2V_SIZES = set(SUPPORTED_SIZES["i2v-A14B"])

jobs: dict[str, dict[str, Any]] = {}
client_jobs: dict[str, str] = {}
jobs_lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wan-i2v")
pipeline: "ResidentWanI2V | None" = None
pipeline_ready = False


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResidentWanI2V(WanI2V):
    """Wan I2V loader that places both DiT experts directly on one GPU.

    The upstream single-GPU constructor materializes the expert models on CPU
    before moving them. That transiently exceeds this host's 48 GiB RAM. The
    service uses the same Wan modules and generation method, but loads BF16
    checkpoints directly onto the target GPU and keeps every model component
    resident between jobs.
    """

    def __init__(self, config: Any, checkpoint_dir: Path, device_id: int) -> None:
        self.device = torch.device(f"cuda:{device_id}")
        self.config = config
        self.rank = 0
        self.t5_cpu = False
        self.init_on_cpu = False
        self.num_train_timesteps = config.num_train_timesteps
        self.boundary = config.boundary
        self.param_dtype = config.param_dtype
        self.vae_stride = config.vae_stride
        self.patch_size = config.patch_size
        self.sp_size = 1
        self.sample_neg_prompt = config.sample_neg_prompt

        logger.info("Loading T5 encoder from %s", checkpoint_dir)
        self.text_encoder = T5EncoderModel(
            text_len=config.text_len,
            dtype=config.t5_dtype,
            device=torch.device("cpu"),
            checkpoint_path=str(checkpoint_dir / config.t5_checkpoint),
            tokenizer_path=str(checkpoint_dir / config.t5_tokenizer),
            shard_fn=None,
        )

        logger.info("Loading VAE on %s", self.device)
        self.vae = Wan2_1_VAE(
            vae_pth=str(checkpoint_dir / config.vae_checkpoint),
            device=self.device,
        )

        self.low_noise_model = self._load_expert(
            checkpoint_dir,
            config.low_noise_checkpoint,
        )
        self.high_noise_model = self._load_expert(
            checkpoint_dir,
            config.high_noise_checkpoint,
        )

        logger.info("Moving T5 encoder to %s", self.device)
        self.text_encoder.model.to(self.device)

    def _load_expert(self, checkpoint_dir: Path, subfolder: str) -> WanModel:
        logger.info("Loading %s directly on %s as %s", subfolder, self.device, self.param_dtype)
        model = WanModel.from_pretrained(
            str(checkpoint_dir),
            subfolder=subfolder,
            torch_dtype=self.param_dtype,
            low_cpu_mem_usage=True,
            device_map={"": str(self.device)},
        )
        return model.eval().requires_grad_(False)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _persist_job(job: dict[str, Any]) -> None:
    job["updated_at"] = utcnow()
    _atomic_write_json(JOBS_DIR / f"{job['id']}.json", job)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in job.items()
        if key not in {"input_file", "output_file"}
    }
    if job.get("status") == "succeeded":
        result["result_url"] = f"/v1/i2v/jobs/{job['id']}/result"
    return result


def _job_snapshot(job_id: str) -> dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _public_job(dict(job))


def _update_job(job_id: str, **changes: Any) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.update(changes)
        _persist_job(job)


def _load_jobs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(JOBS_DIR.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if job.get("status") in {"queued", "running"}:
                job.update(
                    status="failed",
                    finished_at=utcnow(),
                    error="service restarted before the job completed",
                )
                _persist_job(job)
            jobs[str(job["id"])] = job
            if job.get("client_job_id"):
                client_jobs[str(job["client_job_id"])] = str(job["id"])
        except Exception:
            logger.exception("Ignoring unreadable job record %s", path)


def _require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if not API_KEY:
        raise HTTPException(status_code=503, detail="service API key is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="invalid API key")


def _gpu_snapshot() -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {"available": False}
    free_bytes, total_bytes = torch.cuda.mem_get_info(GPU_DEVICE)
    return {
        "available": True,
        "device": GPU_DEVICE,
        "name": torch.cuda.get_device_name(GPU_DEVICE),
        "used_gib": round((total_bytes - free_bytes) / 2**30, 2),
        "free_gib": round(free_bytes / 2**30, 2),
        "total_gib": round(total_bytes / 2**30, 2),
    }


def _component_devices() -> dict[str, str]:
    if pipeline is None:
        return {}
    return {
        "low_noise_model": str(next(pipeline.low_noise_model.parameters()).device),
        "high_noise_model": str(next(pipeline.high_noise_model.parameters()).device),
        "t5": str(next(pipeline.text_encoder.model.parameters()).device),
        "vae": str(pipeline.vae.device),
    }


def _validate_generation_params(
    size: str,
    frames: int,
    steps: int,
    fps: int,
    guidance: float | None,
) -> str:
    normalized_size = size.replace("x", "*")
    if normalized_size not in SUPPORTED_I2V_SIZES:
        raise HTTPException(
            status_code=422,
            detail=f"size must be one of {sorted(SUPPORTED_I2V_SIZES)}",
        )
    if frames < 1 or frames > 161 or (frames - 1) % 4 != 0:
        raise HTTPException(status_code=422, detail="frames must be 4n+1 and between 1 and 161")
    if steps < 1 or steps > 50:
        raise HTTPException(status_code=422, detail="steps must be between 1 and 50")
    if fps < 1 or fps > 60:
        raise HTTPException(status_code=422, detail="fps must be between 1 and 60")
    if guidance is not None and guidance <= 0:
        raise HTTPException(status_code=422, detail="guidance must be greater than zero")
    return normalized_size


def _decode_image(content: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
        return image.convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="image is not a valid image file") from exc


def _run_job(job_id: str) -> None:
    if pipeline is None:
        _update_job(
            job_id,
            status="failed",
            finished_at=utcnow(),
            error="model pipeline is not ready",
        )
        return

    _update_job(job_id, status="running", started_at=utcnow(), error=None)
    with jobs_lock:
        job = dict(jobs[job_id])

    try:
        torch.cuda.set_device(GPU_DEVICE)
        request = job["request"]
        image = Image.open(job["input_file"]).convert("RGB")
        cfg = WAN_CONFIGS["i2v-A14B"]
        seed = int(request["seed"])
        if seed < 0:
            seed = secrets.randbelow(2**63 - 1)
        guide_scale: float | tuple[float, float]
        if request.get("guidance") is None:
            guide_scale = tuple(cfg.sample_guide_scale)
        else:
            guide_scale = float(request["guidance"])
        shift = 3.0 if request["size"] in {"832*480", "480*832"} else float(cfg.sample_shift)

        logger.info(
            "Starting job %s: size=%s frames=%s steps=%s seed=%s",
            job_id,
            request["size"],
            request["frames"],
            request["steps"],
            seed,
        )
        video = pipeline.generate(
            request["prompt"],
            image,
            max_area=MAX_AREA_CONFIGS[request["size"]],
            frame_num=int(request["frames"]),
            shift=shift,
            sample_solver="unipc",
            sampling_steps=int(request["steps"]),
            guide_scale=guide_scale,
            n_prompt=request.get("negative_prompt") or "",
            seed=seed,
            offload_model=False,
        )
        output_file = Path(job["output_file"])
        save_video(
            tensor=video[None],
            save_file=str(output_file),
            fps=int(request["fps"]),
            nrow=1,
            normalize=True,
            value_range=(-1, 1),
        )
        width = int(video.shape[-1])
        height = int(video.shape[-2])
        frame_count = int(video.shape[-3])
        fps = int(request["fps"])
        output_bytes = output_file.stat().st_size
        del video, image
        gc.collect()
        torch.cuda.empty_cache()
        _update_job(
            job_id,
            status="succeeded",
            finished_at=utcnow(),
            seed=seed,
            output_bytes=output_bytes,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration_sec=round(frame_count / fps, 6),
            error=None,
        )
        logger.info("Job %s succeeded: %s bytes", job_id, output_bytes)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        gc.collect()
        torch.cuda.empty_cache()
        _update_job(
            job_id,
            status="failed",
            finished_at=utcnow(),
            error=f"{type(exc).__name__}: {exc}",
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pipeline, pipeline_ready
    if not API_KEY:
        raise RuntimeError("WAN_I2V_API_KEY must be configured")
    if not MODEL_DIR.is_dir():
        raise RuntimeError(f"model directory does not exist: {MODEL_DIR}")
    _load_jobs()
    torch.cuda.set_device(GPU_DEVICE)
    logger.info("Loading Wan2.2 I2V A14B in full GPU-resident mode from %s", MODEL_DIR)
    pipeline = ResidentWanI2V(
        config=WAN_CONFIGS["i2v-A14B"],
        checkpoint_dir=MODEL_DIR,
        device_id=GPU_DEVICE,
    )
    torch.cuda.synchronize(GPU_DEVICE)
    pipeline_ready = True
    logger.info("Model ready: gpu=%s components=%s", _gpu_snapshot(), _component_devices())
    try:
        yield
    finally:
        pipeline_ready = False
        executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="Wan2.2 I2V A14B Provider API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, Any]:
    with jobs_lock:
        queued = sum(job.get("status") == "queued" for job in jobs.values())
        running = next(
            (job_id for job_id, job in jobs.items() if job.get("status") == "running"),
            None,
        )
    return {
        "ok": pipeline_ready,
        "status": "ready" if pipeline_ready else "loading",
        "model": "Wan-AI/Wan2.2-I2V-A14B",
        "mode": "full_gpu_resident",
        "queue_depth": queued,
        "current_job_id": running,
        "gpu": _gpu_snapshot(),
        "components": _component_devices(),
    }


@app.post("/v1/i2v/jobs", dependencies=[Depends(_require_api_key)])
async def submit_job(
    image: UploadFile = File(...),
    prompt: str = Form(..., min_length=1, max_length=8000),
    frames: int = Form(81),
    steps: int = Form(40),
    negative_prompt: str | None = Form(None),
    seed: int = Form(-1),
    fps: int = Form(16),
    guidance: float | None = Form(None),
    size: str = Form("1280*720"),
    client_job_id: str | None = Form(None),
) -> dict[str, Any]:
    if not pipeline_ready:
        raise HTTPException(status_code=503, detail="model is not ready")
    normalized_size = _validate_generation_params(size, frames, steps, fps, guidance)
    if client_job_id:
        with jobs_lock:
            existing_job_id = client_jobs.get(client_job_id)
            if existing_job_id:
                return _job_snapshot(existing_job_id)

    content = await image.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="image is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="image exceeds upload size limit")
    decoded_image = _decode_image(content)

    with jobs_lock:
        active_count = sum(
            job.get("status") in {"queued", "running"}
            for job in jobs.values()
        )
        if active_count >= MAX_QUEUE:
            raise HTTPException(status_code=429, detail="generation queue is full")
        job_id = uuid.uuid4().hex
        input_file = INPUTS_DIR / f"{job_id}.png"
        output_file = OUTPUTS_DIR / f"{job_id}.mp4"
        decoded_image.save(input_file, format="PNG")
        job = {
            "id": job_id,
            "client_job_id": client_job_id,
            "status": "queued",
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "input_file": str(input_file),
            "output_file": str(output_file),
            "request": {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "size": normalized_size,
                "frames": frames,
                "steps": steps,
                "seed": seed,
                "fps": fps,
                "guidance": guidance,
            },
        }
        jobs[job_id] = job
        if client_job_id:
            client_jobs[client_job_id] = job_id
        _persist_job(job)
    executor.submit(_run_job, job_id)
    return _job_snapshot(job_id)


@app.get("/v1/i2v/jobs/{job_id}", dependencies=[Depends(_require_api_key)])
def get_job(job_id: str) -> dict[str, Any]:
    return _job_snapshot(job_id)


@app.get("/v1/i2v/jobs/{job_id}/result", dependencies=[Depends(_require_api_key)])
def get_result(
    job_id: str,
    variant: str = Query(default="native"),
) -> FileResponse:
    if variant != "native":
        raise HTTPException(status_code=422, detail="only variant=native is supported")
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.get("status") != "succeeded":
            raise HTTPException(status_code=409, detail="video is not ready")
        output_file = Path(str(job["output_file"]))
    if not output_file.is_file():
        raise HTTPException(status_code=404, detail="result file is missing")
    return FileResponse(
        output_file,
        media_type="video/mp4",
        filename=f"{job_id}.mp4",
    )
