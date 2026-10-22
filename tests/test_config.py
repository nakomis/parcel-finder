import subprocess

import pytest

from parcel_finder.config import MissingSecret, secret


def test_secret_missing_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 44, "", ""))
    with pytest.raises(MissingSecret):
        secret("nope", "nope")


def test_secret_returns_stripped_value(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "s3cret\n", ""))
    assert secret("svc", "acct") == "s3cret"
