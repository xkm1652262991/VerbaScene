import json
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import JobRecord


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.jobs_dir = root / "jobs"
        self.inputs_dir = root / "inputs"
        self.outputs_dir = root / "outputs"
        self.logs_dir = root / "logs"
        for path in (self.jobs_dir, self.inputs_dir, self.outputs_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)

    def job_file(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save(self, job: JobRecord) -> None:
        job.updated_at = utcnow()
        self.job_file(job.id).write_text(
            job.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def get(self, job_id: str) -> JobRecord | None:
        path = self.job_file(job_id)
        if not path.exists():
            return None
        return JobRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for path in sorted(self.jobs_dir.glob("*.json"), reverse=True):
            try:
                jobs.append(JobRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return jobs
