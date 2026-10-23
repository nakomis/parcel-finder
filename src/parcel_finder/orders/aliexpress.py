"""AliExpress Open Platform: signing, tokens and order lookup (Dropshipping API)."""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import config
from .model import Order

REFRESH_MARGIN_MS = 2 * 3600 * 1000
# gmt_create is China time: order 3076931683840500's gmt_create 05:54:41 = paid 21:54:49Z.
AE_TZ = ZoneInfo("Asia/Shanghai")


class Reauthorise(Exception):
    """The refresh token has lapsed (or there are no tokens): run `parcel-finder authorise`."""


class ApiError(Exception):
    pass


def sign(params: dict[str, str], secret: str, api_path: str = "") -> str:
    base = api_path + "".join(k + params[k] for k in sorted(params))
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def _post(url: str, params: dict[str, str]) -> dict:
    req = urllib.request.Request(url, urllib.parse.urlencode(params).encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except ValueError:
            raise ApiError(f"HTTP {e.code} from {url}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ApiError(f"{url}: {e}") from None


class KeychainTokenStore:
    def get(self, account: str) -> str | None:
        try:
            return config.secret(config.AE_SERVICE, account)
        except config.MissingSecret:
            return None

    def put(self, account: str, value: str) -> None:
        config.store_secret(config.AE_SERVICE, account, value)


class MemoryStore:
    def __init__(self, values: dict[str, str]):
        self.values = dict(values)

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value


class AliExpress:
    def __init__(self, key: str, secret: str, store=None):
        self.key, self.secret = key, secret
        self.store = store or KeychainTokenStore()

    @classmethod
    def from_keychain(cls) -> "AliExpress":
        return cls(config.secret(config.AE_SERVICE, "app-key"), config.secret(config.AE_SERVICE, "app-secret"))

    def _base(self) -> dict[str, str]:
        return {"app_key": self.key, "timestamp": str(int(time.time() * 1000)), "sign_method": "sha256"}

    def _save(self, r: dict) -> None:
        if not r.get("access_token"):
            raise Reauthorise(f"token call failed: {r.get('message') or r.get('code')}")
        self.store.put("access-token", r["access_token"])
        self.store.put("refresh-token", r["refresh_token"])
        self.store.put("token-meta", json.dumps({"expire_time": r["expire_time"],
                                                 "refresh_token_valid_time": r["refresh_token_valid_time"]}))

    def _rest(self, path: str, **extra: str) -> dict:
        p = {**self._base(), **extra}
        p["sign"] = sign(p, self.secret, path)
        return _post(config.AE_REST + path, p)

    def authorise_url(self) -> str:
        return config.AE_AUTHORISE + "?" + urllib.parse.urlencode(
            {"response_type": "code", "force_auth": "true", "redirect_uri": config.AE_REDIRECT, "client_id": self.key})

    def complete(self, code: str) -> None:
        self._save(self._rest("/auth/token/create", code=code))

    def refresh(self) -> None:
        token = self.store.get("refresh-token")
        meta = json.loads(self.store.get("token-meta") or "{}")
        if not token or meta.get("refresh_token_valid_time", float("inf")) <= time.time() * 1000:
            raise Reauthorise("refresh token missing or lapsed")
        self._save(self._rest("/auth/token/refresh", refresh_token=token))

    def _access_token(self) -> str:
        token = self.store.get("access-token")
        meta = json.loads(self.store.get("token-meta") or "{}")
        # No meta yet (tokens from before expiry tracking): refresh once to learn the expiry times.
        if not token or not meta or meta["expire_time"] - REFRESH_MARGIN_MS <= time.time() * 1000:
            self.refresh()
            token = self.store.get("access-token")
        return token

    def _call(self, method: str, **extra: str) -> dict:
        p = {**self._base(), "method": method, "session": self._access_token(), **extra}
        p["sign"] = sign(p, self.secret)
        return _post(config.AE_GATEWAY, p)

    def order_detail(self, oid: str) -> tuple[str, list[Order]]:
        """The order's status and its live lines (cancelled lines left out; none means cancelled)."""
        r = self._call("aliexpress.trade.ds.order.get", single_order_query=json.dumps({"order_id": oid}))
        result = r.get("aliexpress_trade_ds_order_get_response", {}).get("result")
        if not result:
            raise ApiError(f"order {oid}: {r.get('error_response') or r}")
        when = datetime.strptime(result["gmt_create"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=AE_TZ)
        when = when.astimezone(timezone.utc)
        status = result.get("order_status", "")
        return status, [Order("aliexpress", oid, line["product_name"], when, status,
                              str(line.get("product_id") or "") or None)
                        for line in result.get("child_order_list", {}).get("aeop_child_order_info", [])
                        if line.get("end_reason") != "CANCELED"]

    def _order(self, oid: str) -> list[Order]:
        return self.order_detail(oid)[1]

    def orders(self, order_ids: list[str]) -> list[Order]:
        return [o for oid in order_ids for o in self._order(oid)]

    def orders_tolerant(self, order_ids: list[str]) -> tuple[list[Order], dict[str, str]]:
        """Like orders(), but one bad order doesn't lose the rest: returns the orders and
        {order_id: problem} for those the API couldn't answer. Reauthorise still propagates."""
        out, problems = [], {}
        for oid in order_ids:
            try:
                out += self._order(oid)
            except (ApiError, KeyError, ValueError, OSError) as e:  # URLError is an OSError
                problems[oid] = str(e)
        return out, problems
