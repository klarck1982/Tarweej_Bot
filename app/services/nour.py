"""عميل Nour Ads API (Meta Collective Ads API v1.5) + وضع المحاكاة (DRY RUN).

القاعدة: كل ما يخرج إلى نور يمرّ من هنا. في وضع المحاكاة لا يُرسل أي شيء إلى الإنترنت،
وتُصنع ردود مطابقة لشكل ردود نور الحقيقية حتى تتصرّف بقية الشيفرة بالطريقة نفسها في الحالتين.

    NOUR_DRY_RUN=1 (أو التوكن فارغ)  →  DryRunNour
    NOUR_DRY_RUN=0 + NOUR_ADS_TOKEN  →  NourClient (HTTP حقيقي)

الأخطاء موحّدة في NourError(code) بأكواد نور نفسها:
unauthorized · forbidden · invalid_platform · budget_too_low · insufficient_balance · duplicate_request ·
not_found · rate_limited · network · bad_response
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from decimal import Decimal
from typing import Any

import aiohttp

from app.config import settings

log = logging.getLogger("nour")

BASE_URL = "https://nour-ads.com/wp-json/mca/v1"
TIMEOUT = aiohttp.ClientTimeout(total=25)

# حالات نور كما في الوثائق (approved = in_progress قديم)
NOUR_STATUSES = ("pending_admin", "in_progress", "approved", "active", "paused", "completed", "rejected")
NON_RETRYABLE = {"invalid_platform", "budget_too_low", "not_found", "bad_request", "validation_error",
                 # حقول ناقصة/غير صالحة — إعادة الإرسال بنفس البيانات لن تنجح (رموز ووردبريس + الشائعة)
                 "missing_field", "missing_fields", "invalid_field", "invalid_param", "invalid_params",
                 "rest_missing_callback_param", "rest_invalid_param", "invalid_telegram_username",
                 "invalid_whatsapp_number", "invalid_duration", "invalid_goal", "invalid_targeting"}


class NourError(Exception):
    def __init__(self, code: str, message: str = "", http: int = 0, details: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.http = http
        self.details = details or {}
        super().__init__(f"{code} ({http}): {message}")

    @property
    def retryable(self) -> bool:
        return self.code not in NON_RETRYABLE


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


class NourClient:
    """الاتصال الحقيقي — لا يُستخدم إلا عندما NOUR_DRY_RUN=0 ويوجد توكن."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    @property
    def dry_run(self) -> bool:
        return False

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=TIMEOUT,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, *, json_body: dict | None = None,
                       headers: dict | None = None, params: dict | None = None) -> dict:
        sess = await self._sess()
        try:
            async with sess.request(method, BASE_URL + path, json=json_body, headers=headers, params=params) as r:
                raw = await r.text()
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    raise NourError("bad_response", raw[:200], r.status) from None
                if r.status >= 400 or body.get("success") is False:
                    code = str(body.get("code") or body.get("error") or {401: "unauthorized", 403: "forbidden",
                                                                              404: "not_found", 429: "rate_limited"}.get(r.status, "bad_request"))
                    msg = str(body.get("message") or body.get("error_description") or raw[:200])
                    details = body.get("details") or body.get("data") or {}
                    raise NourError(code, msg, r.status, details if isinstance(details, dict) else {})
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise NourError("network", str(e)[:200]) from e

    async def get_account(self) -> dict:
        body = await self._request("GET", "/account")
        data = body.get("data", {})
        bal = (data.get("balance") or {}).get("amount")
        return {"balance": _num(bal), "raw": data}

    async def create_campaign(self, payload: dict, idempotency_key: str) -> dict:
        body = await self._request("POST", "/campaigns", json_body=payload,
                                   headers={"Idempotency-Key": idempotency_key, "Content-Type": "application/json"})
        data = body.get("data", {})
        return {"id": str(data.get("id")), "charged": _num(data.get("charged")), "raw": data}

    async def get_campaign(self, nour_id: str) -> dict:
        body = await self._request("GET", f"/campaigns/{nour_id}")
        data = body.get("data", {})
        return {"status": data.get("status"), "spent": _num(data.get("total_spent")),
                "charged": _num(data.get("budget_charged")), "start": data.get("start_date"),
                "end": data.get("end_date"), "raw": data}

    async def list_campaigns(self, status: str | None = None, page: int = 1, per_page: int = 50) -> list[dict]:
        params = {"page": page, "per_page": per_page}
        if status:
            params["status"] = status
        body = await self._request("GET", "/campaigns", params=params)
        return list(body.get("data") or [])

    async def find_by_title(self, title: str) -> dict | None:
        """بعد duplicate_request: نبحث عن الحملة بعنوانها (ORD-{id}) في أول 3 صفحات."""
        for page in range(1, 4):
            rows = await self.list_campaigns(page=page)
            for r in rows:
                if r.get("title") == title:
                    return r
            if len(rows) < 50:
                break
        return None


class DryRunNour:
    """محاكاة كاملة: يردّ كما يردّ نور، ولا يتصل بأي شيء.

    - create_campaign: يخصم افتراضياً charged = ما نتوقعه (يمكن للأدمن لاحقاً محاكاة الحالات بالأزرار).
    - get_campaign: يعيد الحالة المخزّنة في قاعدة البيانات (nour_status) — المحاكاة تغيّرها بأزرار الأدمن.
    """

    _seq = itertools.count(1)

    def __init__(self) -> None:
        self.fail_next: str | None = None  # للاختبارات: يجعل الطلب التالي يفشل بكود معيّن

    @property
    def dry_run(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def get_account(self) -> dict:
        from app.db.repo import settings as settings_repo
        bal = await settings_repo.get("dry_nour_balance_usd", 100)
        return {"balance": Decimal(str(bal)), "raw": {"simulated": True}}

    async def create_campaign(self, payload: dict, idempotency_key: str) -> dict:
        if self.fail_next:
            code, self.fail_next = self.fail_next, None
            raise NourError(code, "simulated failure", 400, {"balance": 1.0, "required": 9.0})
        daily = Decimal(str(payload.get("budget_daily") or 0))
        if payload.get("platform") == "both":
            daily = Decimal(str(payload.get("budget_daily_fb") or 0)) + Decimal(str(payload.get("budget_daily_ig") or 0))
        budget = daily * int(payload.get("duration_days") or 1)
        charged = (budget * Decimal("1.10")).quantize(Decimal("0.01"))
        nid = f"DRY-{idempotency_key.split('-')[-1]}"
        log.info("DRY RUN create_campaign %s → %s charged=%s", idempotency_key, nid, charged)
        return {"id": nid, "charged": charged,
                "raw": {"id": nid, "message": "Simulated — nothing sent to Nour Ads", "charged": float(charged),
                        "campaign_mode": "your_page", "content_type": "manager_setup", "simulated": True}}

    async def get_campaign(self, nour_id: str) -> dict:
        from app.db import pool as db
        row = await db.fetchrow("SELECT nour_status, charged_usd FROM orders WHERE nour_id = $1", nour_id)
        status = row["nour_status"] if row else "pending_admin"
        return {"status": status, "spent": None, "charged": _num(row["charged_usd"]) if row else None,
                "start": None, "end": None, "raw": {"simulated": True, "status": status}}

    async def list_campaigns(self, status: str | None = None, page: int = 1, per_page: int = 50) -> list[dict]:
        return []

    async def find_by_title(self, title: str) -> dict | None:
        return None


_client: NourClient | DryRunNour | None = None


def client() -> NourClient | DryRunNour:
    global _client
    if _client is None:
        if settings.nour_dry_run or not settings.nour_ads_token:
            _client = DryRunNour()
            log.info("Nour Ads: DRY RUN mode (nothing is sent)")
        else:
            _client = NourClient(settings.nour_ads_token)
            log.info("Nour Ads: LIVE mode")
    return _client


def is_dry_run() -> bool:
    return client().dry_run


async def close() -> None:
    if _client is not None:
        await _client.close()
