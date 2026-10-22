"""One Claude Haiku call that returns schema-valid JSON."""
import base64
import json
import os

import anthropic

from . import config


class Refused(Exception):
    pass


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY") or config.secret(*config.ANTHROPIC_KEYCHAIN)
    return anthropic.Anthropic(api_key=key)


def media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def ask_json(prompt: str, images: list[bytes], schema: dict, max_tokens: int = 2000) -> dict:
    content = [{"type": "image", "source": {"type": "base64", "media_type": media_type(img),
                                            "data": base64.standard_b64encode(img).decode()}}
               for img in images if media_type(img)]
    content.append({"type": "text", "text": prompt})
    response = _client().messages.create(
        model=config.HAIKU_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise Refused(prompt[:80])
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"Haiku returned no answer (stop_reason={response.stop_reason})")
    return json.loads(text)
