"""محاكاة كاملة للخطوة 3: المعالج ← التأكيد ← بطاقة الأدمن ← 🧪 ← رفض/استرداد ← مسودة برصيد ناقص ← استئناف.

تشغيل:  source /tmp/pg_env.sh && python3 tests/_sim/sim_orders.py
"""
import asyncio, os, sys, itertools, json
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", SUPPORT_USERNAME="support_demo", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1")
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
from datetime import datetime
from decimal import Decimal
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery, PhotoSize
from fakesession import FakeSession, sent
from app.db import pool as db
from app.main import build_dispatcher
from app.services import money as money_svc

mid = itertools.count(9000); n = itertools.count(1)
USERNAMES = {555: "samer", 556: None}


def user(uid): return User(id=uid, is_bot=False, first_name="سامر", username=USERNAMES.get(uid))
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
        if name in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageCaption"):
            kb = d.get("reply_markup") or {}; rows = kb.get("inline_keyboard") or kb.get("keyboard") or []
            txt = (d.get("text") or d.get("caption") or "").replace("\u2800", "").replace("\u200b", "").strip()
            print(f"  [{name}→{d.get('chat_id')}] {txt[: (2000 if full else 300)].replace(chr(10), ' / ')}")
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + b['text'] for b in r))
        elif name == "AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:120]}")
        elif name == "EditMessageReplyMarkup": print("  [buttons removed]")
    sent.clear()


async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM users WHERE tg_id IN (555,556,999)")
    await db.execute("UPDATE settings SET value = '\"rafat_admin\"'::jsonb WHERE key='admin_fallback_username'")
    await db.execute("UPDATE settings SET value = jsonb_set(value, '{usdt_trc20,address}', '\"TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE\"') WHERE key='payment_methods'")
    await db.execute("DELETE FROM settings WHERE key = 'x'")  # no-op
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()
    async def step(l, u, full=False): await dp.feed_update(bot, u); show(l, full)
    U = 555; A = 999
    for uid in (U, A):
        await dp.feed_update(bot, msg(uid, "/start")); await dp.feed_update(bot, cb(uid, "nav:accept"))
    sent.clear()
    # رصيد للعميل 30$
    from app.db.repo import users
    await users.upsert_user(U, "سامر", "samer")
    await money_svc.credit(U, Decimal("30"), "topup", note="sim")

    await step("القائمة الرئيسية (B)", msg(U, "🏠 القائمة"))
    await step("M0 الباقات", cb(U, "nav:meta"))
    await step("M1 المنصة — تجربة (بلا كلاهما)", cb(U, "meta:pkg:trial"))
    await step("M2 الهدف", cb(U, "meta:plat:facebook"))
    await step("M3 الدولة", cb(U, "meta:goal:messages"))
    await step("M3 محافظات سوريا", cb(U, "meta:ctry:SY"))
    await dp.feed_update(bot, cb(U, "meta:prov:damascus")); sent.clear()
    await step("محافظتان محددتان", cb(U, "meta:prov:aleppo"))
    await step("M4 الجمهور", cb(U, "meta:prov_done"))
    await dp.feed_update(bot, cb(U, "meta:gender:female")); sent.clear()
    await step("عمر 25–45", cb(U, "meta:age:25:45"))
    await step("M5 الرابط", cb(U, "meta:aud_done"))
    await step("رابط خاطئ", msg(U, "صفحتي على فيسبوك"))
    await step("رابط صحيح", msg(U, "facebook.com/abaya.dimashq/posts/123"))
    await step("وصف قصير جداً", msg(U, "عبايات"))
    await step("وصف صحيح", msg(U, "متجر عبايات في دمشق، جمهوري نساء 20-45، عرض خصم 20% حتى نهاية الشهر"))
    await step("صورة مرفقة", msg(U, photo=True))
    await step("تم — نص إعلاني؟", cb(U, "meta:media_done"))
    await step("لا نص", cb(U, "meta:addon:copy:0"))
    await step("واتساب خاطئ", msg(U, "12345"))
    await step("واتساب محلي", msg(U, "٠٩٣٣ ١٢٣ ٤٥٦"))
    await step("M7 الملخص (عنده معرّف)", cb(U, "meta:wa_ok"), full=True)
    await step("M8 تأكيد", cb(U, "meta:confirm"), full=True)
    o = await db.fetchrow("select * from orders where id=1")
    print("ORDER 1:", o["status"], o["nour_id"], o["price_usd"], o["cost_usd"], o["charged_usd"], "payload:", json.dumps(json.loads(o["nour_payload"]), ensure_ascii=False))
    bal = await db.fetchval("select balance_usd from users where tg_id=555"); print("balance after:", bal)

    await step("الأدمن: 🧪 نور قَبِل", cb(A, "adm:ord:1:sim:in_progress"))
    await step("الأدمن: 🧪 انطلق", cb(A, "adm:ord:1:sim:active"))
    await step("العميل: طلباتي", cb(U, "nav:orders"))
    await step("العميل: تفاصيل الطلب", cb(U, "ord:view:1"), full=True)
    await step("الأدمن: 🧪 اكتمل", cb(A, "adm:ord:1:sim:completed"))

    # طلب ثانٍ: احتراف + كلاهما → رصيد ناقص → مسودة → شحن → استئناف
    await step("M0 مرة ثانية", cb(U, "nav:meta"))
    await step("احتراف: المنصة (كلاهما متاح)", cb(U, "meta:pkg:pro"))
    await dp.feed_update(bot, cb(U, "meta:plat:both")); await dp.feed_update(bot, cb(U, "meta:goal:post_promotion"))
    await step("دول أخرى", cb(U, "meta:ctry_page:1"))
    await step("السعودية (بلا محافظات → الجمهور)", cb(U, "meta:ctry:SA"))
    await dp.feed_update(bot, cb(U, "meta:aud_done")); await dp.feed_update(bot, msg(U, "https://instagram.com/p/xyz"))
    await dp.feed_update(bot, msg(U, "مطعم شاورما في الرياض، نبي طلبات توصيل")); sent.clear()
    await step("تخطي الملفات → النص الإعلاني", cb(U, "meta:media_done"))
    await step("نعم نص (+5$)", cb(U, "meta:addon:copy:1"))
    await dp.feed_update(bot, msg(U, "+966501234567")); sent.clear()
    await step("الملخص — رصيد ناقص → مسودة", cb(U, "meta:wa_ok"), full=True)
    d = await db.fetchrow("select id,status,price_usd,cost_usd from orders where status='awaiting_payment'"); print("DRAFT:", dict(d) if d else None)
    await step("اشحن الفرق (يقترح المبلغ)", cb(U, "meta:topup_gap"))
    await step("يختار TRC20", cb(U, "bal:m:usdt_trc20"))
    await money_svc.credit(U, Decimal("60"), "topup", note="sim topup")  # الأدمن اعتمد
    await step("رصيدي (يظهر المسودة)", cb(U, "bal:menu"))
    await step("أكمل طلبي المعلّق", cb(U, "ord:resume"), full=True)
    await step("تأكيد المسودة", cb(U, "meta:confirm"))
    o2 = await db.fetchrow("select * from orders where id=2"); print("ORDER 2:", o2["status"], o2["price_usd"], o2["cost_usd"], "payload:", json.dumps(json.loads(o2["nour_payload"]), ensure_ascii=False))
    await step("الأدمن: 🧪 رفض → استرداد", cb(A, "adm:ord:2:sim:rejected"))
    o2 = await db.fetchrow("select status, refunded_usd from orders where id=2"); print("ORDER 2 after reject:", dict(o2))

    # مستخدم بلا معرّف
    U2 = 556
    await dp.feed_update(bot, msg(U2, "/start")); await dp.feed_update(bot, cb(U2, "nav:accept")); sent.clear()
    await money_svc.credit(U2, Decimal("10"), "topup", note="sim")
    await dp.feed_update(bot, cb(U2, "meta:pkg:custom")); sent.clear()
    await step("مخصص: يومي مكتوب", msg(U2, "٢.٥"))
    await step("أيام مكتوبة خاطئة", msg(U2, "40"))
    await step("أيام 1", msg(U2, "1"))
    await dp.feed_update(bot, cb(U2, "meta:plat:instagram")); await dp.feed_update(bot, cb(U2, "meta:goal:reach"))
    await dp.feed_update(bot, cb(U2, "meta:ctry:EG")); await dp.feed_update(bot, cb(U2, "meta:aud_done"))
    await dp.feed_update(bot, msg(U2, "https://facebook.com/x")); await dp.feed_update(bot, msg(U2, "صفحة أخبار مصرية نريد وصولاً"))
    await dp.feed_update(bot, cb(U2, "meta:media_done")); await dp.feed_update(bot, cb(U2, "meta:addon:copy:0"))
    await dp.feed_update(bot, msg(U2, "00201001234567")); sent.clear()
    await step("بلا معرّف → شاشة الإرشاد", cb(U2, "meta:wa_ok"))
    await step("ضبطته؟ (لسّا لا)", cb(U2, "meta:uname_check"))
    await step("متابعة بدون معرّف → ملخص", cb(U2, "meta:uname_skip"), full=True)
    await step("تأكيد (2.5$×1 = 3.25$)", cb(U2, "meta:confirm"))
    o3 = await db.fetchrow("select * from orders where id=3"); print("ORDER 3:", o3["status"], o3["price_usd"], o3["cost_usd"], "payload:", json.dumps(json.loads(o3["nour_payload"]), ensure_ascii=False))

    # الأدمن: القائمة + بطاقة + استرداد يدوي
    await step("الأدمن: لوحة", msg(A, "/admin"))
    await step("الأدمن: الطلبات المفتوحة", cb(A, "adm:orders"))
    await step("الأدمن: بطاقة #3", cb(A, "adm:ord:3:view"), full=True)
    await step("الأدمن: استرداد #3", cb(A, "adm:ord:3:refund"))
    await step("الأدمن: السبب", msg(A, "العميل طلب الإلغاء قبل الانطلاق"))

    for uid in (U, U2):
        bal = await db.fetchval("select balance_usd from users where tg_id=$1", uid); ls = await money_svc.ledger_sum(uid)
        print(f"user {uid}: رصيد {bal} | دفتر {ls} | متطابق: {bal == ls}")
    print("orders:", [(r['id'], r['status'], str(r['price_usd']), str(r['refunded_usd'])) for r in await db.fetch("select * from orders order by id")])
    print("ledger:", [(r['type'], str(r['amount_usd']), r['note']) for r in await db.fetch("select * from ledger order by id")])
    await db.close_pool()

asyncio.run(main())
