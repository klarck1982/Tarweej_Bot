"""محاكاة الخطوة 4 (v0.7.0) — Nour Ads «حقيقي» ضد خادم وهمي محلي يطبّق وثائق API v1.5 حرفياً:
  إقلاع (فحص التوكن) ← Cpanel: اختبار الاتصال ← طلب Meta كامل يُرسل فعلياً (Idempotency-Key) ← رصيد نور لا يكفي:
  الطلب ينتظر + تنبيه فوري للأدمن + طمأنة للعميل ← شحن نور ← «عاد الرصيد» + إعادة إرسال تلقائية ← مزامنة الحالات
  (pending_admin → in_progress → active → completed) ← رفض نور = استرداد ← تنبيه رصيد منخفض + تذكير ← التقرير اليومي
  ← 🧨 التصفير: ممنوع مع حملات مفتوحة، مسموح بعدها، العدّادات تعود إلى 1.

تشغيل:  source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/sim_nour_live.py
"""
import asyncio, os, sys, itertools, json, hmac, hashlib, time
from urllib.parse import urlencode
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="0", NOUR_ADS_TOKEN="mca_live_simtoken", SUPPORT_USERNAME="support_demo", TZ="Asia/Damascus")
from datetime import datetime
from decimal import Decimal
import aiohttp
from aiohttp import web
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery, PhotoSize
from fakesession import FakeSession, sent
from app.db import pool as db
from app.main import build_dispatcher, on_startup
from app.services import money as money_svc, pricing as P, cpanel as CP, scheduler, nour, nour_health as NH, launch_reset as LR
from app.web import make_app
from app.web_cpanel import setup_cpanel
from app.db.repo import orders as orders_repo, users

# ═══════════ خادم Nour الوهمي ═══════════
NOUR = {"balance": Decimal("5.00"), "campaigns": {}, "seq": itertools.count(5100), "keys": {}, "log": []}
def _err(code, msg, status=400, details=None):
    body = {"success": False, "code": code, "message": msg}
    if details: body["details"] = details
    return web.json_response(body, status=status)
async def n_account(req):
    if req.headers.get("Authorization") != "Bearer mca_live_simtoken": return _err("unauthorized", "bad token", 401)
    return web.json_response({"success": True, "data": {"client": {"id": 7, "name": "Tarweej Bot", "slug": "tarweej"}, "balance": {"amount": str(NOUR["balance"]), "currency": "USD"}}})
async def n_create(req):
    if req.headers.get("Authorization") != "Bearer mca_live_simtoken": return _err("unauthorized", "bad token", 401)
    key = req.headers.get("Idempotency-Key"); body = await req.json(); NOUR["log"].append((key, body))
    if key in NOUR["keys"]: return _err("duplicate_request", "same key")
    daily = Decimal(str(body.get("budget_daily") or 0))
    if body.get("platform") == "both": daily = Decimal(str(body["budget_daily_fb"])) + Decimal(str(body["budget_daily_ig"]))
    cost = (daily * int(body["duration_days"]) * Decimal("1.10")).quantize(Decimal("0.01"))
    if NOUR["balance"] < cost: return _err("insufficient_balance", "Insufficient balance", 400, {"balance": float(NOUR["balance"]), "required": float(cost)})
    NOUR["balance"] -= cost; cid = next(NOUR["seq"]); NOUR["keys"][key] = cid
    NOUR["campaigns"][cid] = {"id": cid, "title": body.get("title"), "status": "pending_admin", "budget_charged": float(cost), "total_spent": 0.0, "platform": body.get("platform")}
    return web.json_response({"success": True, "data": {"id": cid, "message": "created", "charged": float(cost), "campaign_mode": "your_page", "content_type": body.get("content_type")}}, status=201)
async def n_get(req):
    c = NOUR["campaigns"].get(int(req.match_info["cid"]))
    return web.json_response({"success": True, "data": c}) if c else _err("not_found", "no", 404)
async def n_list(req):
    return web.json_response({"success": True, "data": list(NOUR["campaigns"].values()), "meta": {"page": 1, "per_page": 50, "total": len(NOUR["campaigns"]), "pages": 1}})
def nour_app():
    a = web.Application(); a.router.add_get("/wp-json/mca/v1/account", n_account); a.router.add_post("/wp-json/mca/v1/campaigns", n_create)
    a.router.add_get("/wp-json/mca/v1/campaigns/{cid}", n_get); a.router.add_get("/wp-json/mca/v1/campaigns", n_list); return a

# ═══════════ أدوات تيليغرام الوهمية ═══════════
mid = itertools.count(9000); n = itertools.count(1)
A = 999; U = 555
def user(uid): return User(id=uid, is_bot=False, first_name="رأفت" if uid == A else "سامر", username="rafat" if uid == A else "samer")
def msg(uid, text=None, photo=False):
    kw = dict(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=user(uid))
    if photo: kw["photo"] = [PhotoSize(file_id="AgACAgQAAxkBAAI_photo", file_unique_id="u1", width=800, height=600)]
    else: kw["text"] = text
    return Update(update_id=next(n), message=Message(**kw))
def cb(uid, data):
    m = Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=User(id=1, is_bot=True, first_name="b"), text="x")
    return Update(update_id=next(n), callback_query=CallbackQuery(id=str(next(n)), from_user=user(uid), chat_instance="ci", message=m, data=data))
COL = {"success": "🟩", "danger": "🟥", "primary": "🟦"}
def show(label, full=False):
    print(f"\n── {label}")
    for name, d in sent:
        if name in ("SendMessage", "EditMessageText"):
            kb = d.get("reply_markup") or {}; rows = kb.get("inline_keyboard") or []
            txt = (d.get("text") or "").replace("\u2800", "").replace("\u200b", "").strip()
            print(f"  [{name}→{d.get('chat_id')}] {txt[: (1500 if full else 260)].replace(chr(10), ' / ')}")
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + b['text'] + (" ↗" if b.get("url") or b.get("web_app") else "") for b in r))
        elif name == "AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:160]}")
    sent.clear()
def init_data(uid):
    u = json.dumps({"id": uid, "first_name": "رأفت"}, ensure_ascii=False)
    params = {"auth_date": str(int(time.time())), "query_id": "AAE", "user": u}
    dcs = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", b"123456:TESTTOKEN", hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)

async def main():
    nour.BASE_URL = "http://127.0.0.1:18094/wp-json/mca/v1"
    nr = web.AppRunner(nour_app()); await nr.setup(); await web.TCPSite(nr, "127.0.0.1", 18094).start()
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM users WHERE tg_id IN (555,999)"); await db.execute("DELETE FROM partner_channels")
    await db.execute("DELETE FROM settings WHERE key IN ('channels','services','pricing','nour_health','low_balance_threshold_usd')")
    from app.db.repo import settings as srepo; srepo.invalidate(); await P.refresh(); await CP.refresh_runtime()
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()
    app = make_app(); setup_cpanel(app, bot)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", 18093).start()
    base = "http://127.0.0.1:18093"
    async def step(l, u, full=False): await dp.feed_update(bot, u); show(l, full)
    for uid in (U, A):
        await dp.feed_update(bot, msg(uid, "/start")); await dp.feed_update(bot, cb(uid, "nav:accept"))
    sent.clear()
    print("nour mode:", "DRY" if nour.is_dry_run() else "LIVE ✅")

    print("\n══ 0) الإقلاع: فحص التوكن")
    await on_startup(bot, []); show("رسائل الإقلاع", full=True)

    async with aiohttp.ClientSession() as http:
        async def post(path, body=None, expect=200):
            async with http.post(f"{base}/cpanel/api/{path}", json=body or {}, headers={"X-Telegram-Init-Data": init_data(A)}) as r:
                data = await r.json(); tag = "✅" if r.status == expect else "❌"
                print(f"  {tag} POST {path} → {r.status}" + (f" | {data.get('message')}" if data.get('message') else ""))
                return data
        print("\n══ 1) Cpanel: بطاقة Nour + اختبار الاتصال")
        snap = await post("snapshot"); print("  snapshot.nour:", {k: snap["nour"][k] for k in ("dry", "token_set", "balance", "account")}, "| stats.nour_dry =", snap["stats"]["nour_dry"], "nour_balance =", snap["stats"]["nour_balance"])
        r = await post("nour/test"); print("  test:", r["result"]["ok"], r["result"]["account"], r["result"]["balance"])
        old = nour.BASE_URL; nour.BASE_URL = "http://127.0.0.1:1/x"; r = await post("nour/test", expect=400); print("  test (سيرفر ساقط):", r["result"]["error"]); nour.BASE_URL = old
        print("  reset.allowed (لا حملات):", snap["reset"]["allowed"], "| counts:", snap["reset"]["counts"])

        print("\n══ 2) طلب Meta حقيقي — رصيد نور 5$ فقط (التكلفة 11$) → ينتظر")
        await money_svc.credit(U, Decimal("40"), "topup", note="sim")
        await dp.feed_update(bot, cb(U, "nav:meta")); await dp.feed_update(bot, cb(U, "meta:pkg:trial")); await dp.feed_update(bot, cb(U, "meta:plat:facebook"))
        await dp.feed_update(bot, cb(U, "meta:goal:messages")); await dp.feed_update(bot, cb(U, "meta:ctry:SY")); await dp.feed_update(bot, cb(U, "meta:prov:damascus")); await dp.feed_update(bot, cb(U, "meta:prov_done"))
        await dp.feed_update(bot, cb(U, "meta:aud_done")); await dp.feed_update(bot, msg(U, "facebook.com/abaya.dimashq/posts/123"))
        await dp.feed_update(bot, msg(U, "متجر عبايات في دمشق، جمهوري نساء 20-45، عرض خصم 20% حتى نهاية الشهر")); await dp.feed_update(bot, cb(U, "meta:media_done"))
        await dp.feed_update(bot, cb(U, "meta:addon:copy:0")); await dp.feed_update(bot, msg(U, "0933123456")); await dp.feed_update(bot, cb(U, "meta:wa_ok")); sent.clear()
        await step("M8 تأكيد → الإرسال الفعلي يفشل برصيد نور", cb(U, "meta:confirm"), full=True)
        o = await orders_repo.get(1); print(f"  ORD-1: status={o['status']} nour_id={o['nour_id']} note={o['note']} next_retry={o['next_retry_at'] is not None} | nour.log keys={[k for k, _ in NOUR['log']]}")
        print("  payload sent:", json.dumps(NOUR["log"][0][1], ensure_ascii=False)[:300])

        print("\n══ 3) المجدول: تنبيه رصيد منخفض ← شحن نور ← «عاد الرصيد» ← إعادة إرسال تلقائية")
        await NH.watch_balance(bot, force=True); show("فحص الرصيد (5$ < 50$)", full=True)
        await NH.watch_balance(bot, force=True); print("  (فحص ثانٍ فوري: رسائل =", len(sent), "— لا تكرار)"); sent.clear()
        await db.execute("UPDATE settings SET value = jsonb_set(value, '{low_reminded_at}', to_jsonb((now() - interval '25 hours')::text)) WHERE key='nour_health'"); srepo.invalidate()
        await NH.watch_balance(bot, force=True); show("بعد 25 ساعة: تذكير")
        NOUR["balance"] = Decimal("120.00")
        await NH.watch_balance(bot, force=True); show("بعد شحن نور: عاد الرصيد")
        await scheduler.tick(bot); show("دورة المجدول: إعادة الإرسال + بطاقة الأدمن", full=True)
        o = await orders_repo.get(1); print(f"  ORD-1: status={o['status']} nour_id={o['nour_id']} charged={o['charged_usd']} attempts={o['submit_attempts']} | nour balance={NOUR['balance']}")
        print("  Idempotency keys used:", [k for k, _ in NOUR["log"]], "| campaigns:", {k: v['status'] for k, v in NOUR['campaigns'].items()})

        print("\n══ 4) مزامنة الحالات من نور (pending_admin → in_progress → active → completed)")
        scheduler._last_sync = None
        cid = int(o["nour_id"])
        for st_ in ("in_progress", "active", "completed"):
            NOUR["campaigns"][cid]["status"] = st_; NOUR["campaigns"][cid]["total_spent"] = 6.5 if st_ != "in_progress" else 0
            scheduler._last_sync = None; await scheduler.tick(bot); show(f"نور: {st_}", full=(st_ == "active"))
        o = await orders_repo.get(1); print(f"  ORD-1 final: status={o['status']} nour_status={o['nour_status']} started={o['started_at'] is not None} completed={o['completed_at'] is not None}")

        print("\n══ 5) طلب ثانٍ: نور يرفض → استرداد تلقائي + التصفير ممنوع أثناء الحملة المفتوحة")
        await dp.feed_update(bot, cb(U, "nav:meta")); await dp.feed_update(bot, cb(U, "meta:pkg:trial")); await dp.feed_update(bot, cb(U, "meta:plat:instagram"))
        await dp.feed_update(bot, cb(U, "meta:goal:reach")); await dp.feed_update(bot, cb(U, "meta:ctry:EG")); await dp.feed_update(bot, cb(U, "meta:aud_done"))
        await dp.feed_update(bot, msg(U, "https://instagram.com/p/xyz")); await dp.feed_update(bot, msg(U, "حساب ملابس أطفال في القاهرة — عرض العودة للمدارس على كل التشكيلة"))
        await dp.feed_update(bot, cb(U, "meta:media_done")); await dp.feed_update(bot, cb(U, "meta:addon:copy:0")); await dp.feed_update(bot, msg(U, "0933123456")); await dp.feed_update(bot, cb(U, "meta:wa_ok")); sent.clear()
        await step("تأكيد → يُرسل فوراً (الرصيد يكفي)", cb(U, "meta:confirm"))
        o2 = await orders_repo.get(2); print(f"  ORD-2: status={o2['status']} nour_id={o2['nour_id']} balance_user={await users.get_balance(U)}")
        pv = await LR.preview(); print("  reset.allowed أثناء حملة مفتوحة:", pv["allowed"], "|", pv["blockers"])
        await post("reset/execute", {"confirm": "تصفير"}, expect=400)
        NOUR["campaigns"][int(o2["nour_id"])]["status"] = "rejected"; scheduler._last_sync = None
        await scheduler.tick(bot); show("نور رفض → استرداد", full=True)
        o2 = await orders_repo.get(2); print(f"  ORD-2: status={o2['status']} refunded={o2['refunded_usd']} balance_user={await users.get_balance(U)}")

        print("\n══ 6) التقرير اليومي + بطاقة الطلب الحقيقية (بلا أزرار 🧪)")
        await db.execute("UPDATE orders SET charged_usd = 12.10 WHERE id = 1")  # فرق تكلفة مصطنع للتقرير
        text = await NH.daily_report(bot, force=True); show("التقرير", full=True)
        r = await post("nour/report"); print("  report via cpanel: ok =", r["ok"], "| report_date =", r["nour"]["report_date"])
        await step("الأدمن: الطلبات المفتوحة", cb(A, "adm:orders"))
        await db.execute("UPDATE orders SET status='active', nour_status='active' WHERE id = 1"); await step("بطاقة ORD-1 (حقيقي)", cb(A, "adm:ord:1"))
        await step("🔄 تحديث من نور", cb(A, "adm:ord:1:sync"))
        o = await orders_repo.get(1); print("  after sync from nour:", o["status"])

        print("\n══ 7) 🧨 التصفير")
        await db.execute("UPDATE orders SET status='completed' WHERE id = 1")
        await CP.save_partner_channel({"title": "قناة تجريبية", "url": "@test_ch", "price_24h": "3"}, admin_id=A)
        pv = await post("reset/preview"); print("  preview:", pv["counts"], "allowed =", pv["allowed"], "balances =", pv["client_balances"])
        await post("reset/execute", {"confirm": "تصفر"}, expect=400)
        r = await post("reset/execute", {"confirm": "تصفير", "wipe_partner": True}); show("إشعار التصفير", full=True)
        print("  before:", r["before"], "| snapshot.stats users:", (await post("stats", {"period": "all"}))["users_total"])
        counts = {t: await db.fetchval(f"SELECT count(*) FROM {t}") for t in ("users", "orders", "topups", "ledger", "events", "partner_channels", "fsm_state")}
        print("  after:", counts, "| pricing kept:", (await post("snapshot"))["pricing"]["meta"]["mult"], "| audit:", (await post("snapshot"))["audit"][0]["key"])
        await money_svc.credit(A, Decimal("1"), "topup", note="post-reset")
        await db.execute("INSERT INTO orders(user_id,kind,status,spec,price_usd,cost_usd) VALUES (999,'tg_ads','draft','{}',1,1)")
        print("  first ids after reset: order =", await db.fetchval("SELECT max(id) FROM orders"), "| ledger =", await db.fetchval("SELECT max(id) FROM ledger"))
        await step("الأدمن ما زال يعمل بعد التصفير", msg(A, "/admin"))
    await runner.cleanup(); await nr.cleanup(); await nour.close(); await db.close_pool()

asyncio.run(main())
