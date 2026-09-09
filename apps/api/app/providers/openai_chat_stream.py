from __future__ import annotations

import json
from time import monotonic
from typing import Any, BinaryIO

from app.providers.types import ProviderProgressCallback, SubmissionState


def read_openai_chat_stream(
    response: BinaryIO,
    *,
    on_progress: ProviderProgressCallback | None,
    progress_interval_sec: float,
) -> dict[str, Any]:
    """Aggregate an OpenAI-compatible SSE chat stream into one response object."""
    started_at = monotonic()
    last_progress_at = started_at
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    chunk_count = 0
    response_id = ""
    response_model = ""
    response_created: int | None = None
    response_object = "chat.completion"
    system_fingerprint: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    saw_sse_event = False
    received_done = False

    _emit_progress(
        on_progress,
        event="provider_accepted",
        started_at=started_at,
        chunk_count=0,
        content_chars=0,
        reasoning_chars=0,
    )

    while True:
        raw_line = response.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            if not saw_sse_event and line.startswith("{"):
                remainder = response.read().decode("utf-8")
                return json.loads(f"{line}{remainder}")
            continue

        saw_sse_event = True
        data = line[5:].strip()
        if data == "[DONE]":
            received_done = True
            break
        if not data:
            continue
        chunk = json.loads(data)
        if isinstance(chunk.get("error"), dict):
            error = chunk["error"]
            raise ValueError(str(error.get("message") or error))

        chunk_count += 1
        response_id = str(chunk.get("id") or response_id)
        response_model = str(chunk.get("model") or response_model)
        if isinstance(chunk.get("created"), int):
            response_created = chunk["created"]
        response_object = str(chunk.get("object") or response_object).replace(
            ".chunk", ""
        )
        if chunk.get("system_fingerprint") is not None:
            system_fingerprint = str(chunk["system_fingerprint"])
        if isinstance(chunk.get("usage"), dict):
            usage = dict(chunk["usage"])

        choices = chunk.get("choices") or []
        if choices and isinstance(choices[0], dict):
            choice = choices[0]
            delta_value = choice.get("delta")
            message_value = choice.get("message")
            if isinstance(delta_value, dict):
                delta: dict[str, Any] = delta_value
            elif isinstance(message_value, dict):
                delta = message_value
            else:
                delta = {}
            content = _text_delta(delta.get("content"))
            reasoning = _text_delta(
                delta.get("reasoning_content")
                if delta.get("reasoning_content") is not None
                else delta.get("reasoning")
            )
            if content:
                content_parts.append(content)
            if reasoning:
                reasoning_parts.append(reasoning)
            if choice.get("finish_reason") is not None:
                finish_reason = str(choice["finish_reason"])

        now = monotonic()
        if now - last_progress_at >= max(0.25, progress_interval_sec):
            _emit_progress(
                on_progress,
                event="stream_delta",
                started_at=started_at,
                chunk_count=chunk_count,
                content_chars=sum(map(len, content_parts)),
                reasoning_chars=sum(map(len, reasoning_parts)),
            )
            last_progress_at = now

    if not saw_sse_event:
        raise ValueError("OpenAI-compatible stream returned no SSE events")
    if not received_done and finish_reason is None:
        raise ValueError("OpenAI-compatible stream ended before completion")

    content = "".join(content_parts)
    reasoning_content = "".join(reasoning_parts)
    _emit_progress(
        on_progress,
        event="stream_complete",
        started_at=started_at,
        chunk_count=chunk_count,
        content_chars=len(content),
        reasoning_chars=len(reasoning_content),
    )
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    result: dict[str, Any] = {
        "id": response_id,
        "object": response_object,
        "model": response_model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": message,
            }
        ],
        "_stream": {
            "chunk_count": chunk_count,
            "content_chars": len(content),
            "reasoning_chars": len(reasoning_content),
        },
    }
    if response_created is not None:
        result["created"] = response_created
    if system_fingerprint is not None:
        result["system_fingerprint"] = system_fingerprint
    if usage is not None:
        result["usage"] = usage
    return result


def _emit_progress(
    callback: ProviderProgressCallback | None,
    *,
    event: str,
    started_at: float,
    chunk_count: int,
    content_chars: int,
    reasoning_chars: int,
) -> None:
    if callback is None:
        return
    callback(
        {
            "event": event,
            "submission_state": SubmissionState.ACCEPTED.value,
            "elapsed_sec": round(max(0.0, monotonic() - started_at), 1),
            "chunk_count": chunk_count,
            "content_chars": content_chars,
            "reasoning_chars": reasoning_chars,
        }
    )


def _text_delta(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)
