#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _encode_image_data_uri(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def _build_payload(model: str, prompt: str, image_path: Path | None) -> dict[str, Any]:
    if image_path is None:
        content: str | list[dict[str, Any]] = prompt
    else:
        data_uri = _encode_image_data_uri(image_path)
        content = [
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": data_uri,
                    "detail": "auto",
                },
            },
        ]

    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }


def _post_json(url: str, api_key: str, payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return json.loads(text)
    except urllib.error.HTTPError as exc:
        err_text = exc.read().decode("utf-8", errors="replace")
        try:
            return {"http_status": exc.code, "error_body": json.loads(err_text)}
        except json.JSONDecodeError:
            return {"http_status": exc.code, "error_body": err_text}


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct Z.AI chat.completions test without Strands.")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""), help="Z.AI API key")
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.z.ai/api/paas/v4"),
        help="Base URL (example: https://api.z.ai/api/paas/v4)",
    )
    parser.add_argument(
        "--path",
        default="/chat/completions",
        help="Endpoint path (default: /chat/completions)",
    )
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL_ID", "glm-4.7"))
    parser.add_argument("--prompt", default="この画像に何が写っているか説明してください。")
    parser.add_argument("--image", type=Path, default=None, help="Path to image file")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: API key is required. Set --api-key or OPENAI_API_KEY.", file=sys.stderr)
        return 2
    if args.image is not None and not args.image.exists():
        print(f"ERROR: image not found: {args.image}", file=sys.stderr)
        return 2

    url = args.base_url.rstrip("/") + "/" + args.path.lstrip("/")
    payload = _build_payload(args.model, args.prompt, args.image)

    result = _post_json(url, args.api_key, payload, args.timeout)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
