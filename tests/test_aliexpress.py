import json
import time
from pathlib import Path

import pytest

from parcel_finder.orders import aliexpress
from parcel_finder.orders.aliexpress import AliExpress, MemoryStore, Reauthorise, sign

FIXTURE = Path(__file__).parent / "fixtures" / "ae_order.json"
DAY = 86_400_000


def now_ms():
    return int(time.time() * 1000)


def test_sign_business_call():
    p = {"app_key": "12345", "timestamp": "1700000000000", "sign_method": "sha256", "method": "aliexpress.ds.category.get"}
    assert sign(p, "testsecret") == "79BCF4A71F7E5BCA5E3D9A5F836BFFC7EFD7BF26EA98346E6C9DB777D77447F1"


def test_sign_rest_call_prefixes_path():
    p = {"app_key": "12345", "timestamp": "1700000000000", "sign_method": "sha256", "code": "abc"}
    assert sign(p, "testsecret", "/auth/token/create") == "D3DDCDF3953B4DC26EFC38A6F89959B7D4F25D3542072143FF53D963A5BBC8B0"


@pytest.fixture
def fake_http(monkeypatch):
    calls = []

    def post(url, params):
        calls.append((url, params))
        if url.endswith("/auth/token/refresh"):
            return {"access_token": "new-at", "refresh_token": "new-rt",
                    "expire_time": now_ms() + DAY, "refresh_token_valid_time": now_ms() + 2 * DAY}
        return json.loads(FIXTURE.read_text())

    monkeypatch.setattr(aliexpress, "_post", post)
    return calls


def valid_store():
    return MemoryStore({"access-token": "at", "refresh-token": "rt",
                        "token-meta": json.dumps({"expire_time": now_ms() + DAY, "refresh_token_valid_time": now_ms() + 2 * DAY})})


def test_orders_skip_cancelled_lines(fake_http):
    orders = AliExpress("k", "s", store=valid_store()).orders(["1"])
    assert [o.title for o in orders] == [
        "Estardyn Active/Passive Dual Mode Buzzer Module 5V, High/Low Level Trigger & PWM for Raspberry Pi ESP32"]
    o = orders[0]
    assert o.shop == "aliexpress" and o.order_id == "1" and o.product_id == "1005011855197311"
    assert o.ordered_at.year == 2026 and o.status == "WAIT_BUYER_ACCEPT_GOODS"


def test_orders_send_session_and_signature(fake_http):
    AliExpress("k", "s", store=valid_store()).orders(["1"])
    url, params = fake_http[0]
    assert params["session"] == "at" and params["method"] == "aliexpress.trade.ds.order.get"
    assert json.loads(params["single_order_query"]) == {"order_id": "1"} and params["sign"]


def test_refresh_when_access_token_near_expiry(fake_http):
    store = MemoryStore({"access-token": "at", "refresh-token": "rt",
                         "token-meta": json.dumps({"expire_time": now_ms() + 60_000, "refresh_token_valid_time": now_ms() + DAY})})
    AliExpress("k", "s", store=store).orders(["1"])
    assert fake_http[0][0].endswith("/auth/token/refresh") and store.get("access-token") == "new-at"


def test_expired_refresh_token_raises_reauthorise(fake_http):   # Review Focus 3
    store = MemoryStore({"access-token": "at", "refresh-token": "rt",
                         "token-meta": json.dumps({"expire_time": now_ms() - DAY, "refresh_token_valid_time": now_ms() - 1})})
    with pytest.raises(Reauthorise):
        AliExpress("k", "s", store=store).orders(["1"])


def test_missing_tokens_raise_reauthorise(fake_http):
    with pytest.raises(Reauthorise):
        AliExpress("k", "s", store=MemoryStore({})).orders(["1"])


def test_authorise_url():
    url = AliExpress("555", "s", store=MemoryStore({})).authorise_url()
    assert url.startswith("https://api-sg.aliexpress.com/oauth/authorize?")
    assert "client_id=555" in url and "redirect_uri=https%3A%2F%2Fnakom.is%2Falifinder" in url and "force_auth=true" in url
