import io
import json
import urllib.error

import pytest

from parcel_finder import google


def http_error(code, reason):
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


def test_get_retries_on_rate_limit(monkeypatch):
    calls, sleeps = [], []

    def urlopen(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise http_error(403, "rateLimitExceeded")
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(google.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(google.time, "sleep", sleeps.append)
    g = google.Gmail()
    g._token, g._expires = "t", 9e12
    assert g._get("messages", []) == {"ok": True} and len(calls) == 3 and sleeps and sleeps[1] > sleeps[0]


def test_get_does_not_retry_other_403(monkeypatch):
    monkeypatch.setattr(google.urllib.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(http_error(403, "insufficientPermissions")))
    monkeypatch.setattr(google.time, "sleep", lambda s: None)
    g = google.Gmail()
    g._token, g._expires = "t", 9e12
    with pytest.raises(urllib.error.HTTPError):
        g._get("messages", [])
