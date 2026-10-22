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


def ask_json(prompt: str, images: list[bytes], schema: dict, max_tokens: int = 2000) -> dict:
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.standard_b64encode(img).decode()}}
               for img in images]
    content.append({"type": "text", "text": prompt})
    response = _client().messages.create(
        model=config.HAIKU_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise Refused(prompt[:80])
    return json.loads(next(b.text for b in response.content if b.type == "text"))
