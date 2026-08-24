import asyncio
import os
import signal
from pathlib import Path

from app.config import settings
from app.schemas import JobRecord
from app.store import JobStore, utcnow


class WanRunner:
    def __init__(self, store: JobStore):
        self.store = store
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.current_job_id: str | None = None
        self.current_process: asyncio.subprocess.Process | None = None

    async def enqueue(self, job_id: str) -> None:
        await self.queue.put(job_id)

    def build_command(self, job: JobRecord) -> list[str]:
        output_path = Path(job.output_path)
        image_path = Path(job.image_path)
        gpu_count = len(job.gpu_devices.split(","))

        common = [
            "generate.py",
            "--task",
            "i2v-A14B",
            "--size",
            job.size,
            "--ckpt_dir",
            str(settings.wan_ckpt_dir),
            "--image",
            str(image_path),
            "--prompt",
            job.prompt,
            "--save_file",
            str(output_path),
        ]
        if job.frame_num is not None:
            common.extend(["--frame_num", str(job.frame_num)])
        if job.sample_steps is not None:
            common.extend(["--sample_steps", str(job.sample_steps)])
        if job.seed is not None:
            common.extend(["--base_seed", str(job.seed)])

        if job.mode == "multi_fsdp":
            return [
                settings.torchrun_bin,
                "--nproc_per_node",
                str(gpu_count),
                *common,
                "--dit_fsdp",
                "--t5_fsdp",
                "--ulysses_size",
                str(gpu_count),
            ]

        return [
            settings.python_bin,
            *common,
            "--offload_model",
            "True",
            "--convert_model_dtype",
            "--t5_cpu",
        ]

    async def worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self.run_job(job_id)
            finally:
                self.queue.task_done()

    async def run_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None or job.status == "cancelled":
            return

        self.current_job_id = job_id
        job.status = "running"
        job.started_at = utcnow()
        command = self.build_command(job)
        job.command = command
        self.store.save(job)

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = job.gpu_devices
        env.setdefault("PYTHONUNBUFFERED", "1")

        log_path = Path(job.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with log_path.open("ab") as log_file:
                log_file.write(("COMMAND: " + " ".join(command) + "\n").encode("utf-8"))
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(settings.wan_repo_dir),
                    env=env,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                self.current_process = process
                try:
                    return_code = await asyncio.wait_for(
                        process.wait(),
                        timeout=settings.request_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGTERM)
                    return_code = 124

            latest = self.store.get(job_id)
            if latest is None:
                return
            latest.return_code = return_code
            latest.finished_at = utcnow()
            if return_code == 0 and Path(latest.output_path).exists():
                latest.status = "succeeded"
            else:
                latest.status = "failed"
                latest.error = f"Wan2.2 command exited with code {return_code}"
            self.store.save(latest)
        except Exception as exc:
            latest = self.store.get(job_id)
            if latest is not None:
                latest.status = "failed"
                latest.finished_at = utcnow()
                latest.error = str(exc)
                self.store.save(latest)
        finally:
            self.current_process = None
            self.current_job_id = None

    async def cancel(self, job_id: str) -> bool:
        job = self.store.get(job_id)
        if job is None:
            return False

        if self.current_job_id == job_id and self.current_process is not None:
            try:
                os.killpg(self.current_process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        job.status = "cancelled"
        job.finished_at = utcnow()
        self.store.save(job)
        return True
