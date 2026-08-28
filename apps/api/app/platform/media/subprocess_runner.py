"""Small cancellable subprocess runner for local media task handlers."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Callable


class MediaProcessCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaProcessResult:
    returncode: int
    output_tail: str


def run_cancellable_media_process(
    args: list[str],
    *,
    cancel_requested: Callable[[], bool],
    timeout_sec: float,
    poll_interval_sec: float = 0.25,
) -> MediaProcessResult:
    """Run without a shell, periodically observing the durable cancel flag."""
    log_handle = tempfile.NamedTemporaryFile(
        prefix="verbascene-media-process-",
        suffix=".log",
        delete=False,
    )
    log_path = Path(log_handle.name)
    try:
        process = subprocess.Popen(
            args,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        started_at = monotonic()
        while process.poll() is None:
            if cancel_requested():
                _terminate_process(process)
                raise MediaProcessCancelled("Media task was cancelled")
            if monotonic() - started_at >= max(1.0, timeout_sec):
                _terminate_process(process)
                raise TimeoutError(f"Media process exceeded {timeout_sec:g} seconds")
            sleep(max(0.05, poll_interval_sec))
        log_handle.flush()
        return MediaProcessResult(
            returncode=int(process.returncode or 0),
            output_tail=_read_tail(log_path),
        )
    finally:
        log_handle.close()
        log_path.unlink(missing_ok=True)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _read_tail(path: Path, limit: int = 4000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
