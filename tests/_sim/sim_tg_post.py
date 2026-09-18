"""محاكاة 📝 القنوات الشريكة (v0.6.0):
Cpanel API (إضافة/تعديل/إيقاف/حذف/تحقق) ← الزر يُفتح في البوت ← معالج TP1–TP7 مع صورة ← بطاقة الأدمن ← تأكيد موعد ← تم النشر
← مشاهدات ← إنهاء تلقائي (المجدول) ← طلب ثانٍ: إلغاء العميل قبل الجدولة (استرداد) ← طلب ثالث برصيد ناقص → مسودة → استئناف
← رفض الأدمن (استرداد) ← إيقاف كل القنوات → الزر يعود مقفولاً.

تشغيل:  source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/sim_tg_post.py
"""
import asyncio, os, sys, itertools, json, hmac, hashlib, time
from urllib.parse import urlencode
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1", SUPPORT_USERNAME="support_demo", TZ="Asia/Damascus")
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import aiohttp
from aiohttp import web
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery, PhotoSize
from fakesession import FakeSession, sent
from app.db import pool as db
from app.main import build_dispatcher
from app.services import money as money_svc, pricing as P, cpanel as CP, scheduler
from app.web import make_app
from app.web_cpanel import setup_cpanel
from app.db.repo import users, orders as orders_repo, partner_channels as PC

mid = itertools.count(9000); n = itertools.count(1)
A = 999; U = 555
def user(uid): return User(id=uid, is_bot=False, first_name="رأفت" if uid == A else "سامر", username="rafat" if uid == A else "samer")
def msg(uid, text):
    return Update(update_id=next(n), message=Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=user(uid), text=text))
def photo(uid, caption=None):
    ph = [PhotoSize(file_id="AgACAgQAAxkBAAIB_photo_big", file_unique_id="u1", width=800, height=600, file_size=1000)]
    return Update(update_id=next(n), message=Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=user(uid), photo=ph, caption=caption))
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
            print(f"  [{name}→{d.get('chat_id')}] {txt[: (1400 if full else 240)].replace(chr(10), ' / ')}")
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + b['text'] + (" ↗" if b.get("url") else "") for b in r))
        elif name in ("SendPhoto", "SendMediaGroup", "SendVideo"): print(f"  [{name}→{d.get('chat_id')}] {d.get('caption') or ''}")
        elif name == "AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:160]}")
        elif name == "EditMessageReplyMarkup": print("  [buttons removed]")
    sent.clear()

def init_data(uid, age=0):
    u = json.dumps({"id": uid, "first_name": "رأفت" if uid == A else "سامر", "username": "rafat"}, ensure_ascii=False)
    params = {"auth_date": str(int(time.time()) - age), "query_id": "AAE", "user": u}
    dcs = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", b"123456:TESTTOKEN", hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)

async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM partner_channels"); await db.execute("DELETE FROM users WHERE tg_id IN (555,999)")
    await db.execute("DELETE FROM settings WHERE key IN ('channels','services','pricing')")
    from app.db.repo import settings as srepo; srepo.invalidate(); await P.refresh(); await CP.refresh_runtime()
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()
    app = make_app(); setup_cpanel(app, bot)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", 18097).start()
    base = "http://127.0.0.1:18097"
    async def step(l, u, full=False): await dp.feed_update(bot, u); show(l, full)
    for uid in (U, A):
        await dp.feed_update(bot, msg(uid, "/start")); await dp.feed_update(bot, cb(uid, "nav:accept"))
    sent.clear()
    await money_svc.credit(U, Decimal("40"), "topup", note="sim")

    async with aiohttp.ClientSession() as http:
        async def post(path, body=None, expect=200):
            async with http.post(f"{base}/cpanel/api/{path}", json=body or {}, headers={"X-Telegram-Init-Data": init_data(A)}) as r:
                data = await r.json(); tag = "✅" if r.status == expect else "❌"
                print(f"  {tag} POST {path} → {r.status}" + (f" | {data.get('message')}" if data.get('message') else ""))
                return data

        print("\n── 0) قبل أي قناة: الزر مقفول")
        await step("T0", cb(U, "nav:tg"))
        await step("ضغط الزر المقفول", cb(U, "tgp:start"))
        snap = await post("snapshot"); print("  service_locked:", snap["service_locked"], "| partner.live =", snap["partner"]["live"])

        print("\n── 1) Cpanel: إضافة قنوات + تحقق")
        await post("partner/save", {"title": "س", "url": "@souq_sham", "price_24h": "12"}, expect=400)
        await post("partner/save", {"title": "سوق الشام", "url": "not a link", "price_24h": "12"}, expect=400)
        await post("partner/save", {"title": "سوق الشام", "url": "@souq_sham", "price_24h": ""}, expect=400)
        await post("partner/save", {"title": "سوق الشام", "url": "@souq_sham", "price_24h": "12", "subscribers": "abc"}, expect=400)
        r1 = await post("partner/save", {"title": "سوق الشام", "url": "https://t.me/souq_sham", "category": "shopping", "subscribers": "45000", "avg_views": "9000",
                                         "blurb": "أكبر قناة عروض في دمشق — منشورات يومية وجمهور مهتم بالتسوق.", "price_24h": "12", "price_48h": "18", "price_pin": "",
                                         "allow_pin": True, "owner_contact": "@sham_owner", "notes": "النشر بعد 6 مساءً"})
        c1 = r1["channel"]; print("  quotes:", c1["quotes"], "| locked now:", r1["service_locked"])
        r2 = await post("partner/save", {"title": "عروض حلب", "url": "@aleppo_offers", "category": "shopping", "subscribers": "30000", "price_24h": "8", "allow_pin": False})
        c2 = r2["channel"]; print("  quotes (بلا تثبيت):", c2["quotes"])
        r3 = await post("partner/save", {"title": "تك بالعربي", "url": "t.me/tech_ar", "category": "tech", "subscribers": "22000", "price_24h": "10", "price_pin": "20"})
        c3 = r3["channel"]; print("  quotes (مثبت بسعر خاص 20):", c3["quotes"])
        r4 = await post("partner/save", {"title": "دليل المتاجر", "url": "@stores_guide", "category": "shopping", "subscribers": "18000", "price_24h": "5", "enabled": False})
        print("  live =", r4["partner"]["live"], "| month =", r4["partner"]["month"])
        r = await post("partner/save", {"id": c2["id"], "title": "عروض حلب", "url": "@aleppo_offers", "category": "shopping", "subscribers": "31000", "price_24h": "8.5", "allow_pin": False})
        print("  edited:", r["channel"]["title"], r["channel"]["price_24h"], r["channel"]["subscribers"])
        await post("save/services", {"tg_post": False}); await post("save/services", {"tg_post": True})

        print("\n── 2) البوت: الزر مفتوح — المعالج TP1→TP7")
        await step("T0 (مفتوح)", cb(U, "nav:tg"))
        await step("ℹ️ الأسعار", cb(U, "info:ads"), full=True)
        await step("TP1 الفئات", cb(U, "tgp:start"))
        await step("TP2 فئة تسوق", cb(U, "tgp:cat:shopping"))
        await step("بطاقة سوق الشام", cb(U, f"tgp:ch:{c1['id']}"), full=True)
        await step("اختيار القناة → TP3", cb(U, f"tgp:pick:{c1['id']}"))
        await step("صيغة مثبت", cb(U, "tgp:fmt:pin"))
        await step("صورة مع تعليق", photo(U, "🔥 افتتاح فرعنا الجديد بالمزة — خصم 30% على كل الأصناف حتى الجمعة! 📍 المزة"))
        await step("التالي → TP5", cb(U, "tgp:content:next"))
        await step("وقت محدد", cb(U, "tgp:when:type"))
        await step("غداً 8 مساءً → الملخص", msg(U, "غداً 8 مساءً"), full=True)
        await step("تعديل → القناة", cb(U, "tgp:edit"))
        await step("رجوع للملخص", cb(U, "tgp:back:summary"))
        await step("تأكيد ودفع", cb(U, "tgp:confirm"), full=True)
        o1 = await orders_repo.get(1); print(f"  ORD-1: status={o1['status']} price={o1['price_usd']} cost={o1['cost_usd']} spec.format={o1['spec']['format']} media={len(await orders_repo.media(1))} balance={await users.get_balance(U)}")

        print("\n── 3) الأدمن: البطاقة → تأكيد موعد → تم النشر → مشاهدات → إنهاء تلقائي")
        await step("طلباتي (عميل)", cb(U, "ord:view:1"), full=True)
        await step("الأدمن: تأكيد الموعد", cb(A, "adm:tgp:1:when"))
        await step("موعد غير مفهوم", msg(A, "بعدين"))
        await step("موعد: غداً 20:00", msg(A, "غداً 20:00"), full=True)
        o1 = await orders_repo.get(1); print(f"  scheduled_at={o1['scheduled_at']} status={o1['status']}")
        await step("العميل يحاول الإلغاء بعد الجدولة", cb(U, "tgp:cancel_order:1"))
        await step("الأدمن: تم النشر", cb(A, "adm:tgp:1:url"))
        await step("رابط خاطئ", msg(A, "example.com/post"))
        await step("رابط صحيح", msg(A, "https://t.me/souq_sham/1287"), full=True)
        o1 = await orders_repo.get(1); print(f"  status={o1['status']} post_url={o1['post_url']} ends_at-started={(o1['ends_at']-o1['started_at'])}")
        await step("العميل: عرض الطلب المنشور", cb(U, "ord:view:1"), full=True)
        await step("الأدمن: مشاهدات", cb(A, "adm:tgp:1:views"))
        await step("8400", msg(A, "8400"))
        # الإنهاء التلقائي: نرجّع ends_at للماضي ونشغّل دورة المجدول
        await db.execute("UPDATE orders SET ends_at = now() - interval '1 minute' WHERE id = 1")
        await scheduler.tick(bot); show("دورة المجدول: إنهاء تلقائي", full=True)
        o1 = await orders_repo.get(1); print(f"  status={o1['status']} results={o1['results']}")

        print("\n── 4) طلب ثانٍ: 24 ساعة + اكتبولي النص → إلغاء العميل قبل الجدولة")
        await step("TP1", cb(U, "tgp:start"))
        await step("كل القنوات", cb(U, "tgp:cat:all"))
        await step("اختيار عروض حلب", cb(U, f"tgp:pick:{c2['id']}"))
        await step("24 ساعة", cb(U, "tgp:fmt:24h"))
        await step("اكتبولي النص", cb(U, "tgp:addon:copy"))
        await step("التالي", cb(U, "tgp:content:next"))
        await step("أقرب وقت → الملخص", cb(U, "tgp:when:asap"), full=True)
        await step("تأكيد", cb(U, "tgp:confirm"))
        o2 = await orders_repo.get(2); print(f"  ORD-2 price={o2['price_usd']} (8.5×1.25 + 5 = 15.63) cost={o2['cost_usd']} balance={await users.get_balance(U)}")
        await step("الأدمن: أدخل النص", cb(A, "adm:tgp:2:text"))
        await step("نص الفريق", msg(A, "🎉 عروض حلب الأقوى — خصم 30% على كل شيء هذا الأسبوع! زورونا في فرع الجميلية."))
        await step("العميل: عرض الطلب", cb(U, "ord:view:2"))
        await step("العميل: إلغاء", cb(U, "tgp:cancel_order:2"))
        await step("نعم ألغِ", cb(U, "tgp:cancel_yes:2"), full=True)
        o2 = await orders_repo.get(2); print(f"  status={o2['status']} refunded={o2['refunded_usd']} balance={await users.get_balance(U)}")

        print("\n── 5) طلب ثالث برصيد ناقص → مسودة → شحن → استئناف → رفض الأدمن (استرداد)")
        await db.execute("UPDATE users SET balance_usd = 5 WHERE tg_id = 555")  # تفريغ الرصيد إلى 5$ للاختبار
        await step("TP1", cb(U, "tgp:start"))
        await step("تقنية", cb(U, "tgp:cat:tech"))
        await step("اختيار تك", cb(U, f"tgp:pick:{c3['id']}"))
        await step("48h غير متاحة عند هذه القناة", cb(U, "tgp:fmt:48h"))
        await step("مثبت (20$ خاص → 25$)", cb(U, "tgp:fmt:pin"))
        await step("نص", msg(U, "دورة برمجة مجانية للمبتدئين — سجّل الآن!"))
        await step("التالي", cb(U, "tgp:content:next"))
        await step("أقرب وقت → ملخص (ناقص)", cb(U, "tgp:when:asap"), full=True)
        d = await orders_repo.get_awaiting(U); print(f"  draft: id={d['id']} kind={d['kind']} price={d['price_usd']} expires={d['expires_at'] is not None}")
        await money_svc.credit(U, Decimal("30"), "topup", note="sim")
        await step("طلباتي", msg(U, "/orders"))
        await step("استئناف المسودة", cb(U, "ord:resume"), full=True)
        await step("تأكيد", cb(U, "tgp:confirm"))
        o3 = await orders_repo.get(d["id"]); print(f"  ORD-{o3['id']} status={o3['status']} price={o3['price_usd']} balance={await users.get_balance(U)}")
        await step("الأدمن: تعذّر النشر", cb(A, f"adm:tgp:{o3['id']}:reject"))
        await step("السبب", msg(A, "صاحب القناة اعتذر عن المحتوى"), full=True)
        o3 = await orders_repo.get(o3["id"]); print(f"  status={o3['status']} refunded={o3['refunded_usd']} balance={await users.get_balance(U)}")

        print("\n── 6) التذكير قبل الموعد + الإحصائيات + الحذف/الأرشفة + القفل من جديد")
        await db.execute("""INSERT INTO orders(user_id,kind,status,spec,price_usd,cost_usd,paid_at,submitted_at,scheduled_at)
                            VALUES (555,'tg_post','in_progress','{"kind":"tg_post","channel_id":%d,"channel_title":"سوق الشام","format":"24h","hours":24}',15,12,now(),now(),now() + interval '40 minutes')""" % c1["id"])
        await scheduler.tick(bot); show("دورة المجدول: تذكير")
        await scheduler.tick(bot); print("  (دورة ثانية بلا تذكير مكرر:", len([1 for nm, _ in sent if nm == "SendMessage"]), "رسائل)"); sent.clear()
        st = await post("stats", {"period": "7d"}); print(f"  stats: orders={st['orders']} revenue={st['revenue']} profit={st['profit']} posts_waiting={st['posts_waiting']} top={st['top_services']}")
        snap = await post("snapshot"); print("  month:", snap["partner"]["month"], "| audit first:", snap["audit"][0]["section"], snap["audit"][0]["key"])
        r = await post("partner/delete", {"id": c1["id"]}); print("  delete سوق الشام (لها طلبات) →", r["result"])
        r = await post("partner/delete", {"id": r4["channel"]["id"]}); print("  delete دليل المتاجر (بلا طلبات) →", r["result"])
        await post("partner/toggle", {"id": c2["id"], "enabled": False}); r = await post("partner/toggle", {"id": c3["id"], "enabled": False})
        print("  live =", r["partner"]["live"], "| locked:", r["service_locked"])
        await step("T0 بعد إيقاف الكل (مقفول)", cb(U, "nav:tg"))
        await step("الأدمن: الطلبات المفتوحة", cb(A, "adm:orders"))
    await runner.cleanup(); await db.close_pool()

asyncio.run(main())
