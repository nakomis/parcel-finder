import types

import pytest

from parcel_finder import haiku


class FakeClient:
    def __init__(self, response):
        self.response, self.calls = response, []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self.response


def resp(text, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=text)])


def test_ask_json_sends_schema_and_images(monkeypatch):
    fake = FakeClient(resp('{"item": "x"}'))
    monkeypatch.setattr(haiku, "_client", lambda: fake)
    assert haiku.ask_json("q", [b"\xff\xd8jpg"], {"type": "object"}) == {"item": "x"}
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert call["messages"][0]["content"][0]["type"] == "image"
    assert "temperature" not in call


def test_ask_json_raises_on_refusal(monkeypatch):
    monkeypatch.setattr(haiku, "_client", lambda: FakeClient(resp("", stop="refusal")))
    with pytest.raises(haiku.Refused):
        haiku.ask_json("q", [], {"type": "object"})
