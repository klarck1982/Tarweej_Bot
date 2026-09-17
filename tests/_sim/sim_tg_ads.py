"""محاكاة 📣 Telegram Ads: المعالج كاملاً ← تأكيد ← بطاقة الأدمن ← أنشأته ← طلب تعديل نص ← نص جديد ← انطلق ← اكتمل بنتائج
← طلب ثانٍ برصيد ناقص → مسودة → استئناف ← رفض تيليغرام (استرداد) ← تجديد.

تشغيل:  source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/sim_tg_ads.py
"""
import asyncio, os, sys, itertools, json
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1", SUPPORT_USERNAME="support_demo")
from datetime import datetime
from decimal import Decimal
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery
from fakesession import FakeSession, sent
from app.db import pool as db
from app.main import build_dispatcher
from app.services import money as money_svc
from app.db.repo import users

mid = itertools.count(9000); n = itertools.count(1)
A = 999; U = 555
def user(uid): return User(id=uid, is_bot=False, first_name="أدمن" if uid == A else "سامر", username="rafat" if uid == A else "samer")
def msg(uid, text):
    return Update(update_id=next(n), message=Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=user(uid), text=text))
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
            print(f"  [{name}→{d.get('chat_id')}] {txt[: (1200 if full else 220)].replace(chr(10), ' / ')}")
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + b['text'] for b in r))
        elif name == "AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:140]}")
        elif name == "EditMessageReplyMarkup": print("  [buttons removed]")
    sent.clear()

async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM users WHERE tg_id IN (555,999)"); await db.execute("DELETE FROM settings WHERE key='channels'")
    await db.execute("UPDATE settings SET value = jsonb_set(value, '{usdt_trc20,address}', '\"TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE\"') WHERE key='payment_methods'")
    from app.db.repo import settings as srepo; srepo.invalidate()
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()
    async def step(l, u, full=False): await dp.feed_update(bot, u); show(l, full)
    for uid in (U, A):
        await dp.feed_update(bot, msg(uid, "/start")); await dp.feed_update(bot, cb(uid, "nav:accept"))
    sent.clear()
    await money_svc.credit(U, Decimal("40"), "topup", note="sim")

    await step("T0 شاشة تيليغرام", cb(U, "nav:tg"))
    await step("القنوات الشريكة (مقفول)", cb(U, "tgp:start"))
    await step("TA1 الميزانية", cb(U, "tga:start"), full=True)
    await step("مبلغ مكتوب خاطئ", msg(U, "5"))
    await step("مبلغ مكتوب 25", msg(U, "25"))
    await step("TA2 → قنوات محددة", cb(U, "tga:mode:channels"))
    await step("روابط خاطئة", msg(U, "قناة الشام"))
    await step("روابط صحيحة", msg(U, "@souq_alsham\nhttps://t.me/halab_offers\nt.me/souq_alsham"))
    await step("TA3 نص طويل", msg(U, "ن" * 170))
    await step("TA3 نص صحيح", msg(U, "افتتاح فرعنا الجديد في المزة 🎉 خصم 20% على كل الطلبات حتى نهاية الأسبوع — اطلب الآن"))
    await step("TA4 رابط خارجي مرفوض", msg(U, "https://mystore.com"))
    await step("TA4 رابط قناة", msg(U, "@abaya_dimashq"), full=True)
    await step("تعديل → الاستهداف", cb(U, "tga:edit"))
    await step("الاستهداف: اهتمامات", cb(U, "tga:back:mode"))
    await dp.feed_update(bot, cb(U, "tga:mode:interests")); sent.clear()
    await dp.feed_update(bot, cb(U, "tga:int:shopping")); sent.clear()
    await step("اهتمامان", cb(U, "tga:int:food"))
    await step("تم → يرجع للملخص (وضع تعديل)", cb(U, "tga:int_done"), full=True)
    await step("تأكيد 33.75$", cb(U, "tga:confirm"), full=True)
    o = await db.fetchrow("select * from orders where id=1"); print("ORDER 1:", o["kind"], o["status"], o["price_usd"], o["cost_usd"], "spec:", json.dumps(json.loads(o["spec"]), ensure_ascii=False)[:300])
    print("balance:", await db.fetchval("select balance_usd from users where tg_id=555"))

    await step("الأدمن: أنشأته", cb(A, "adm:tga:1:to:in_progress"))
    await step("الأدمن: اطلب تعديل النص", cb(A, "adm:tga:1:to:needs_revision"))
    await step("الأدمن يكتب السبب", msg(A, "تيليغرام رفض كلمة «خصم 20%» — اكتب العرض بدون نسبة"))
    await step("العميل: طلباتي", cb(U, "nav:orders"))
    await step("العميل: تفاصيل (بانتظار تعديلك)", cb(U, "ord:view:1"))
    await step("العميل: إرسال نص جديد", cb(U, "tga:revise:1"))
    await step("نص جديد", msg(U, "افتتاح فرعنا الجديد في المزة 🎉 عروض افتتاح مميزة حتى نهاية الأسبوع — اطلب الآن"))
    await step("الأدمن: أنشأته (بعد التعديل)", cb(A, "adm:tga:1:to:in_progress"))
    await step("الأدمن: انطلق", cb(A, "adm:tga:1:to:active"))
    await step("الأدمن: اكتمل → النتائج", cb(A, "adm:tga:1:to:completed"))
    await step("نتائج خاطئة", msg(A, "كثير"))
    await step("نتائج 12500 340", msg(A, "12500 340"), full=True)
    await step("العميل: تفاصيل الطلب المكتمل", cb(U, "ord:view:1"), full=True)

    # طلب ثانٍ: رصيد ناقص (6.25$ متبقي) → مسودة → شحن → استئناف → رفض
    await dp.feed_update(bot, cb(U, "tga:start")); await dp.feed_update(bot, cb(U, "tga:budget:10")); await dp.feed_update(bot, cb(U, "tga:mode:geo"))
    await step("دولة", cb(U, "tga:ctry:SY"))
    await dp.feed_update(bot, cb(U, "tga:lang:ar")); sent.clear()
    await step("اكتبولي النص (+5$)", cb(U, "tga:addon:copy"))
    await step("رابط بوت → ملخص (رصيد ناقص 18.50 > 6.25)", msg(U, "t.me/my_shop_bot"), full=True)
    d = await db.fetchrow("select id,kind,status,price_usd from orders where status='awaiting_payment'"); print("DRAFT:", dict(d) if d else None)
    await step("اشحن الفرق", cb(U, "meta:topup_gap"))
    await money_svc.credit(U, Decimal("20"), "topup", note="sim")
    await step("رصيدي يظهر المسودة", cb(U, "bal:menu"))
    await step("أكمل طلبي المعلّق (نوع تيليغرام)", cb(U, "ord:resume"), full=True)
    await step("تأكيد", cb(U, "tga:confirm"))
    await step("الأدمن: أدخل النص الذي كتبته", cb(A, "adm:tga:2:text"))
    await step("نص الفريق", msg(A, "بوت متجري يوصل طلبك لباب البيت خلال ساعة 🚚 جرّبه الآن"), full=True)
    await step("الأدمن: رفض تيليغرام", cb(A, "adm:tga:2:to:rejected"))
    await step("سبب الرفض", msg(A, "المحتوى مخالف لسياسة الإعلانات — بوتات المراهنة"))
    print("balance after reject:", await db.fetchval("select balance_usd from users where tg_id=555"))
    await step("العميل: تجديد الطلب 1", cb(U, "ord:renew:1"))
    await step("إلغاء", cb(U, "tga:cancel"))
    await step("الأدمن: لوحة", msg(A, "/admin"))
    await step("الأدمن: الطلبات المفتوحة", cb(A, "adm:orders"))
    bal = await db.fetchval("select balance_usd from users where tg_id=555"); ls = await money_svc.ledger_sum(U)
    print(f"\nuser {U}: رصيد {bal} | دفتر {ls} | متطابق: {bal == ls}")
    print("orders:", [(r['id'], r['kind'], r['status'], str(r['price_usd']), str(r['refunded_usd'])) for r in await db.fetch("select * from orders order by id")])
    await db.close_pool()

asyncio.run(main())
