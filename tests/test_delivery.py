import json
import os
import socket
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from parcel_finder import delivery
from parcel_finder.delivery import LiveSession, copy_photo, live_session, message, send


def test_message_text_exact():
    assert message("USB cable tester", Path("/v/p.jpg")) == (
        "This just arrived! It's been identified as a USB cable tester. What do I do with it? "
        "If you don't recognise it, please let me know!\n\nPhoto: /v/p.jpg")


def test_copy_photo_to_share(tmp_path, monkeypatch, photo_fixture):
    share = tmp_path / "share"
    share.mkdir()
    monkeypatch.setattr(delivery.config, "SHARE_ROOT", share / "parcel-finder")
    dest, shared = copy_photo(photo_fixture, "usb tester!", datetime(2026, 10, 22, 17, 30, 5))
    assert shared and dest == share / "parcel-finder/2026-10-22/173005-usb-tester.jpg" and dest.read_bytes()


def test_copy_photo_falls_back_when_share_missing(tmp_path, monkeypatch, photo_fixture):   # Review Focus 2
    monkeypatch.setattr(delivery.config, "SHARE_ROOT", tmp_path / "not-mounted" / "parcel-finder")
    dest, shared = copy_photo(photo_fixture, "usb-tester", datetime(2026, 10, 22, 17, 30))
    assert not shared and dest == photo_fixture


def test_send_writes_auth_then_user(tmp_path):
    import tempfile
    sock_path = tempfile.mktemp(prefix="pf", suffix=".sock", dir="/tmp")  # AF_UNIX paths max 104 chars on macOS
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    got = []

    def accept():
        conn, _ = srv.accept()
        got.append(conn.makefile().read())
        conn.close()

    t = threading.Thread(target=accept)
    t.start()
    key = tmp_path / "123.abc.key"
    key.write_text("tok\n")
    delivery._send_socket(LiveSession(123, Path(sock_path), key, None, tmp_path, "idle"), "hello")
    t.join(5)
    lines = [json.loads(line) for line in got[0].splitlines()]
    assert lines == [{"type": "auth", "token": "tok"},
                     {"type": "user", "message": {"role": "user", "content": "hello"}}]


def test_live_session_ignores_dead_pid(tmp_path):
    (tmp_path / "999999.json").write_text(json.dumps({"pid": 999999, "sessionId": "s1",
                                                       "messagingSocketPath": "/tmp/x.sock", "cwd": "/"}))
    assert live_session("s1", sessions_dir=tmp_path) is None


def test_live_session_found_for_running_pid(tmp_path):
    pid = os.getpid()
    (tmp_path / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": "s1", "status": "busy",
                                                       "messagingSocketPath": "/tmp/x.sock", "cwd": "/w"}))
    (tmp_path / f"{pid}.deadbeef.key").write_text("k")
    live = live_session("s1", sessions_dir=tmp_path)
    assert live.pid == pid and live.socket == Path("/tmp/x.sock") and live.key_file.name == f"{pid}.deadbeef.key"
    assert live.cwd == Path("/w") and live.status == "busy"


def test_resume_command_quotes_message(monkeypatch):
    seen = []
    monkeypatch.setattr(delivery, "_osascript", lambda script, *args: seen.append(args) or "")
    delivery.resume_in_new_tab(Path("/w d"), "sid", "it's a \"tester\"")
    cmd = seen[0][0]
    assert cmd.startswith("cd '/w d' && claude --resume sid --allow-dangerously-skip-permissions ")
    assert "'it'\"'\"'s a \"tester\"'" in cmd


def _relay_result(result="DONE", is_error=False):
    import json as _json
    return subprocess.CompletedProcess([], 0, _json.dumps({"result": result, "is_error": is_error}), "")


def test_send_relays_through_a_bypass_claude_with_only_sendmessage(tmp_path, monkeypatch):
    from parcel_finder import delivery
    seen = {}
    monkeypatch.setattr(delivery, "_run", lambda cmd, **kw: seen.update(cmd=cmd, env=kw["env"]) or _relay_result())
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "secret")
    delivery.send(LiveSession(1, Path("/tmp/cc-socks/42.sock"), tmp_path / "k", None, tmp_path, "idle"), "It's a buzzer")
    cmd = seen["cmd"]
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--tools") + 1] == "ToolSearch,SendMessage"   # nothing a photo's text could misuse
    assert "uds:/tmp/cc-socks/42.sock" in cmd[-1] and "It's a buzzer" in cmd[-1]
    assert "CLAUDE_CODE_MESSAGING_TOKEN" not in seen["env"]           # the relay is its own session


def test_send_falls_back_to_the_socket_when_the_relay_fails(tmp_path, monkeypatch):
    from parcel_finder import delivery
    raw = []
    monkeypatch.setattr(delivery, "_run", lambda cmd, **kw: _relay_result("SendMessage failed", True))
    monkeypatch.setattr(delivery, "_send_socket", lambda live, text: raw.append(text))
    delivery.send(LiveSession(1, Path("/tmp/cc-socks/42.sock"), tmp_path / "k", None, tmp_path, "idle"), "hi")
    assert raw == ["hi"]
