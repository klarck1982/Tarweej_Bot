"""عميل Nour Ads الحقيقي ضد خادم HTTP وهمي (بلا إنترنت): الترويسات، Idempotency-Key، تفسير الأخطاء، الرصيد.
يحاكي وثائق Meta Collective Ads API v1.5 حرفياً (الشكل success/data، أكواد الأخطاء، details.balance/required)."""
import asyncio
import json
import os

import pytest
from aiohttp import web

os.environ.setdefault("BOT_TOKEN", "1:a")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://x")

from app.services import nour  # noqa: E402

SEEN = {"requests": []}


def _app():
    async def account(req):
        SEEN["requests"].append(("GET", "/account", dict(req.headers)))
        if req.headers.get("Authorization") != "Bearer mca_live_ok":
            return web.json_response({"success": False, "code": "unauthorized", "message": "Invalid token"}, status=401)
        return web.json_response({"success": True, "data": {"client": {"id": 7, "name": "ترويج بوت", "slug": "tarweej", "token_prefix": "mca_live_ok"},
                                                            "user": {"id": 1}, "balance": {"amount": "83.50", "currency": "USD"}}})

    async def campaigns(req):
        body = await req.json()
        SEEN["requests"].append(("POST", "/campaigns", dict(req.headers), body))
        if body.get("budget_daily", 0) < 2:
            return web.json_response({"success": False, "code": "budget_too_low", "message": "min 2"}, status=400)
        if body.get("title") == "ORD-99":
            return web.json_response({"success": False, "code": "insufficient_balance", "message": "no funds",
                                      "details": {"balance": 3.0, "required": 11.0}}, status=400)
        if body.get("title") == "ORD-77":
            return web.json_response({"success": False, "code": "duplicate_request", "message": "dup"}, status=400)
        return web.json_response({"success": True, "data": {"id": 5150, "message": "created", "charged": 11.0,
                                                            "campaign_mode": "your_page", "content_type": "manager_setup"}}, status=201)

    async def campaign(req):
        return web.json_response({"success": True, "data": {"id": int(req.match_info["cid"]), "title": "ORD-1", "status": "active",
                                                            "budget_charged": 11.0, "total_spent": 4.2, "start_date": "2026-09-19", "end_date": "2026-09-24"}})

    async def listing(req):
        rows = [{"id": 5150, "title": "ORD-1", "status": "active"}, {"id": 5151, "title": "ORD-77", "status": "pending_admin", "budget_charged": 6.6}]
        return web.json_response({"success": True, "data": rows, "meta": {"page": 1, "per_page": 50, "total": 2, "pages": 1}})

    app = web.Application()
    app.router.add_get("/wp-json/mca/v1/account", account)
    app.router.add_post("/wp-json/mca/v1/campaigns", campaigns)
    app.router.add_get("/wp-json/mca/v1/campaigns/{cid}", campaign)
    app.router.add_get("/wp-json/mca/v1/campaigns", listing)
    return app


async def _with_server(coro):
    runner = web.AppRunner(_app()); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 18095); await site.start()
    old = nour.BASE_URL
    nour.BASE_URL = "http://127.0.0.1:18095/wp-json/mca/v1"
    try:
        return await coro()
    finally:
        nour.BASE_URL = old
        await runner.cleanup()


def test_account_headers_and_balance():
    async def go():
        c = nour.NourClient("mca_live_ok")
        acc = await c.get_account()
        await c.close()
        return acc
    acc = asyncio.run(_with_server(go))
    assert str(acc["balance"]) == "83.50"
    assert acc["raw"]["client"]["name"] == "ترويج بوت"
    method, path, headers = SEEN["requests"][-1]
    assert headers["Authorization"] == "Bearer mca_live_ok" and headers["Accept"] == "application/json"


def test_bad_token_is_unauthorized():
    async def go():
        c = nour.NourClient("mca_live_wrong")
        try:
            await c.get_account()
        except nour.NourError as e:
            return e
        finally:
            await c.close()
    e = asyncio.run(_with_server(go))
    assert e.code == "unauthorized" and e.http == 401


def test_create_campaign_sends_idempotency_key_and_parses_charged():
    async def go():
        c = nour.NourClient("mca_live_ok")
        res = await c.create_campaign({"content_type": "manager_setup", "platform": "facebook", "budget_daily": 2, "duration_days": 5,
                                       "whatsapp_number": "+963900000000", "telegram_username": "samer", "title": "ORD-1"}, "ord-1")
        await c.close()
        return res
    res = asyncio.run(_with_server(go))
    assert res["id"] == "5150" and str(res["charged"]) == "11.0"
    _, _, headers, body = SEEN["requests"][-1]
    assert headers["Idempotency-Key"] == "ord-1" and headers["Content-Type"] == "application/json"
    assert body["content_type"] == "manager_setup" and body["telegram_username"] == "samer"


def test_error_codes_and_details():
    async def go():
        c = nour.NourClient("mca_live_ok")
        out = {}
        for title, daily in (("ORD-99", 5), ("ORD-77", 5), ("ORD-1", 1)):
            try:
                await c.create_campaign({"budget_daily": daily, "duration_days": 1, "title": title}, f"k-{title}")
            except nour.NourError as e:
                out[title] = e
        found = await c.find_by_title("ORD-77")
        camp = await c.get_campaign("5150")
        await c.close()
        return out, found, camp
    out, found, camp = asyncio.run(_with_server(go))
    assert out["ORD-99"].code == "insufficient_balance" and out["ORD-99"].details == {"balance": 3.0, "required": 11.0}
    assert out["ORD-77"].code == "duplicate_request"
    assert out["ORD-1"].code == "budget_too_low"
    assert found["id"] == 5151
    assert camp["status"] == "active" and str(camp["charged"]) == "11.0" and str(camp["spent"]) == "4.2"


def test_network_error_is_retryable_kind():
    async def go():
        c = nour.NourClient("mca_live_ok")
        old = nour.BASE_URL
        nour.BASE_URL = "http://127.0.0.1:1/wp-json/mca/v1"   # لا شيء يستمع
        try:
            await c.get_account()
        except nour.NourError as e:
            return e
        finally:
            nour.BASE_URL = old
            await c.close()
    e = asyncio.run(go())
    assert e.code == "network"
