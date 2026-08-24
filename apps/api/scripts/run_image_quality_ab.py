from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
sys.path.insert(0, str(API_ROOT))

from app.agents.avd_prompt_pack import (  # noqa: E402
    render_character_reference_prompt,
    render_prop_reference_prompt,
)


DEFAULT_CHARACTER_BASE = "短发小男孩，蓝色T恤，卡其色短裤，红色书包，表情专注温和"
DEFAULT_PROP_BASE = "灰褐色小型鸟，羽毛蓬松微乱，右翼自然下垂，眼神温顺"
DEFAULT_NEGATIVE_PROMPT = (
    "低清晰度，多视图拼贴，分屏，重复主体，额外人物，浮空头部或手部，"
    "额外肢体，画面变形，水印，logo，字幕，乱码文字"
)
DIRECT_OPENER = build_opener(ProxyHandler({}))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the four-image Tom/bird sampling A/B after the upstream image API fixes are live.",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--provider-name", default="openai_image")
    parser.add_argument("--model", default="flux2-klein-base-4b")
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--baseline-quality", choices=("low", "medium", "high", "auto"), default="medium")
    parser.add_argument("--target-quality", choices=("low", "medium", "high", "auto"), default="high")
    parser.add_argument("--guidance", type=float, default=4.0)
    parser.add_argument("--character-seed", type=int, default=20260721)
    parser.add_argument("--prop-seed", type=int, default=20260722)
    parser.add_argument("--character-base", default=DEFAULT_CHARACTER_BASE)
    parser.add_argument("--prop-base", default=DEFAULT_PROP_BASE)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the four request payloads without calling the provider.",
    )
    args = parser.parse_args()

    cases = _build_cases(args)
    if args.dry_run:
        print(json.dumps([case["payload"] for case in cases], ensure_ascii=False, indent=2))
        return

    output_dir = _output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "purpose": "four_image_model_quality_ab",
        "scope": "Tom and injured bird, same prompt and seed per subject, only quality differs",
        "provider_name": args.provider_name,
        "model": args.model,
        "size": args.size,
        "created_at": datetime.now().astimezone().isoformat(),
        "cases": [],
    }

    try:
        for case in cases:
            result = _run_case(
                api_base=args.api_base,
                case=case,
                output_dir=output_dir,
                timeout=args.timeout,
            )
            manifest["cases"].append(result)
            print(f"{result['case_id']}: {result['status']} -> {result.get('file') or result.get('error')}")
    finally:
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"manifest: {manifest_path}")

    failed = [case for case in manifest["cases"] if case.get("status") != "succeeded"]
    if failed:
        raise SystemExit(1)


def _build_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    character_prompt = render_character_reference_prompt(base=args.character_base, style="")
    prop_prompt = render_prop_reference_prompt(base=args.prop_base, style="")
    definitions = [
        (f"tom_{args.baseline_quality}", "character", character_prompt, args.character_seed, args.baseline_quality),
        (f"tom_{args.target_quality}", "character", character_prompt, args.character_seed, args.target_quality),
        (f"bird_{args.baseline_quality}", "prop", prop_prompt, args.prop_seed, args.baseline_quality),
        (f"bird_{args.target_quality}", "prop", prop_prompt, args.prop_seed, args.target_quality),
    ]
    cases = []
    for case_id, subject, prompt, seed, quality in definitions:
        cases.append(
            {
                "case_id": case_id,
                "subject": subject,
                "seed": seed,
                "quality": quality,
                "payload": {
                    "provider_type": "image",
                    "provider_name": args.provider_name,
                    "model": args.model,
                    "prompt": prompt,
                    "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
                    "params": {
                        "size": args.size,
                        "quality": quality,
                        "guidance_scale": args.guidance,
                        "seed": seed,
                    },
                    "metadata": {
                        "diagnostic": "image_quality_ab_v1",
                        "case_id": case_id,
                        "subject": subject,
                    },
                },
            }
        )
    return cases


def _run_case(*, api_base: str, case: dict[str, Any], output_dir: Path, timeout: int) -> dict[str, Any]:
    result = {
        "case_id": case["case_id"],
        "subject": case["subject"],
        "seed": case["seed"],
        "quality": case["quality"],
        "request": case["payload"],
    }
    try:
        response = _post_json(
            f"{api_base.rstrip('/')}/api/providers/test",
            case["payload"],
            timeout=timeout,
        )
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected API response: {_compact(response)}")
        if data.get("status") != "succeeded":
            raise ValueError(_provider_error(data))
        assets = data.get("assets")
        if not isinstance(assets, list) or not assets or not isinstance(assets[0], dict):
            raise ValueError("Provider succeeded without an image asset")
        image_bytes, extension = _read_image_asset(assets[0], timeout=timeout)
        image_path = output_dir / f"{case['case_id']}{extension}"
        image_path.write_bytes(image_bytes)
        result.update(
            {
                "status": "succeeded",
                "file": image_path.name,
                "bytes": len(image_bytes),
                "provider_task_id": data.get("provider_task_id"),
                "asset": _asset_summary(assets[0]),
                "provider_request_echo": _request_echo(data.get("raw_response")),
            }
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        result.update({"status": "failed", "error": str(exc)})
    return result


def _post_json(url: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with DIRECT_OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_image_asset(asset: dict[str, Any], *, timeout: int) -> tuple[bytes, str]:
    uri = asset.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ValueError("Provider asset has no URI")
    mime_type = str(asset.get("mime_type") or "image/png").split(";", maxsplit=1)[0]
    if uri.startswith("data:image/") and ";base64," in uri:
        header, encoded = uri.split(",", maxsplit=1)
        mime_type = header.removeprefix("data:").split(";", maxsplit=1)[0] or mime_type
        return base64.b64decode(encoded), _image_extension(mime_type)
    if uri.startswith(("http://", "https://")):
        with DIRECT_OPENER.open(uri, timeout=timeout) as response:
            body = response.read()
            response_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        return body, _image_extension(response_type or mime_type, uri=uri)
    raise ValueError(f"Unsupported provider asset URI: {uri[:120]}")


def _image_extension(mime_type: str, *, uri: str = "") -> str:
    extension = mimetypes.guess_extension(mime_type) if mime_type else None
    if extension == ".jpe":
        extension = ".jpg"
    if extension in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return extension
    uri_suffix = Path(uri.split("?", maxsplit=1)[0]).suffix.lower()
    return uri_suffix if uri_suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else ".png"


def _asset_summary(asset: dict[str, Any]) -> dict[str, Any]:
    uri = asset.get("uri")
    if isinstance(uri, str) and uri.startswith("data:"):
        uri = f"<omitted data URI {len(uri)} chars>"
    return {
        "asset_type": asset.get("asset_type"),
        "uri": uri,
        "mime_type": asset.get("mime_type"),
        "width": asset.get("width"),
        "height": asset.get("height"),
    }


def _request_echo(raw_response: object) -> object:
    if not isinstance(raw_response, dict):
        return None
    return raw_response.get("request")


def _provider_error(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("error_message") or error.get("error_code") or error)
    return f"Provider status: {data.get('status')}"


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:500]


def _output_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return REPO_ROOT / "storage" / "diagnostics" / "image-quality-ab" / timestamp


if __name__ == "__main__":
    main()
