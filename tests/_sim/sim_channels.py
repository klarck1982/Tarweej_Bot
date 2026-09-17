"""محاكاة قنوات الإدارة: ربط 3 قنوات عبر my_chat_member → بطاقة شحن في قناة الإيداعات → رفض بسبب مكتوب من الخاص
→ بطاقة طلب في قناة الطلبات → 🧪 من داخل القناة → استرداد بسبب من الخاص → تنبيه خطأ إلى قناة التنبيهات → فصل قناة.

تشغيل:  source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/sim_channels.py
"""
import asyncio, os, sys, itertools, json
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1")
from datetime import datetime
from decimal import Decimal
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (Update, Message, Chat, User, CallbackQuery, PhotoSize, ChatMemberUpdated, ChatMemberMember,
                           ChatMemberAdministrator, ChatMemberLeft)
from fakesession import FakeSession, sent, CHANNELS
from app.db import pool as db
from app.main import build_dispatcher
from app.services import money as money_svc
from app.db.repo import users

mid = itertools.count(9000); n = itertools.count(1)
A = 999; U = 555
CH_TOP, CH_ORD, CH_AL = -1001000000001, -1001000000002, -1001000000003

def user(uid): return User(id=uid, is_bot=False, first_name="أدمن" if uid == A else "سامر", username="rafat" if uid == A else "samer")
def msg(uid, text=None, photo=False):
    kw = dict(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=user(uid))
    if photo: kw["photo"] = [PhotoSize(file_id="AgACAgQAAxkBAAI_photo", file_unique_id="u1", width=800, height=600)]
    else: kw["text"] = text
    return Update(update_id=next(n), message=Message(**kw))
def cb(uid, data, chat_id=None, message_id=None):
    chat_id = chat_id or uid
    m = Message(message_id=message_id or next(mid), date=datetime.now(), chat=Chat(id=chat_id, type="channel" if chat_id < 0 else "private", title=CHANNELS.get(chat_id)),
                from_user=User(id=1, is_bot=True, first_name="b"), text="x")
    return Update(update_id=next(n), callback_query=CallbackQuery(id=str(next(n)), from_user=user(uid), chat_instance="ci", message=m, data=data))
def bot_added(chat_id, added=True):
    botu = User(id=1, is_bot=True, first_name="b", username="tarweej_bot")
    adm = ChatMemberAdministrator(user=botu, can_be_edited=False, is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                                  can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False, can_change_info=False,
                                  can_invite_users=False, can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                                  can_send_welcome_messages=False, can_post_messages=True)
    left = ChatMemberLeft(user=botu)
    return Update(update_id=next(n), my_chat_member=ChatMemberUpdated(
        chat=Chat(id=chat_id, type="channel", title=CHANNELS[chat_id]), from_user=user(A), date=datetime.now(),
        old_chat_member=left if added else adm, new_chat_member=adm if added else left))
COL = {"success": "🟩", "danger": "🟥", "primary": "🟦"}
def where(cid):
    return {CH_TOP: "📥قناة الإيداعات", CH_ORD: "📦قناة الطلبات", CH_AL: "🔔قناة التنبيهات", A: "👤خاص الأدمن", U: "🙋العميل"}.get(cid, str(cid))
def show(label, full=False):
    print(f"\n── {label}")
    for name, d in sent:
        if name in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageCaption"):
            kb = d.get("reply_markup") or {}; rows = kb.get("inline_keyboard") or kb.get("keyboard") or []
            txt = (d.get("text") or d.get("caption") or "").replace("\u2800", "").replace("\u200b", "").strip()
            print(f"  [{name}→{where(d.get('chat_id'))}] {txt[: (900 if full else 160)].replace(chr(10), ' / ')}")
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + b['text'] for b in r))
        elif name == "EditMessageReplyMarkup":
            kb = d.get("reply_markup") or {}; rows = kb.get("inline_keyboard") or []
            print(f"  [buttons→{where(d.get('chat_id'))}] " + ("removed" if not rows else ""))
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + b['text'] for b in r))
        elif name == "AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:140]}")
        elif name in ("SendMediaGroup", "SendVideo", "SendDocument"): print(f"  [{name}→{where(d.get('chat_id'))}]")
    sent.clear()

async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); await db.run_migrations()
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM users WHERE tg_id IN (555,999)"); await db.execute("DELETE FROM settings WHERE key='channels'")
    await db.execute("UPDATE settings SET value = jsonb_set(value, '{usdt_trc20,address}', '\"TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE\"') WHERE key='payment_methods'")
    from app.db.repo import settings as srepo; srepo.invalidate()
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()
    async def step(l, u, full=False): await dp.feed_update(bot, u); show(l, full)
    for uid in (U, A):
        await dp.feed_update(bot, msg(uid, "/start")); await dp.feed_update(bot, cb(uid, "nav:accept"))
    sent.clear()

    await step("الإعدادات → قنوات الإدارة (لا شيء مربوط)", cb(A, "adm:ch:menu"))
    await step("كيف أربط؟", cb(A, "adm:ch:help"))
    await step("الأدمن أضاف البوت لقناة الإيداعات", bot_added(CH_TOP))
    await step("يختار: إيداعات", cb(A, f"adm:ch:bind:topups:{CH_TOP}"))
    await dp.feed_update(bot, bot_added(CH_ORD)); await dp.feed_update(bot, cb(A, f"adm:ch:bind:orders:{CH_ORD}"))
    await dp.feed_update(bot, bot_added(CH_AL)); await dp.feed_update(bot, cb(A, f"adm:ch:bind:alerts:{CH_AL}")); sent.clear()
    await step("القائمة بعد الربط", cb(A, "adm:ch:menu"))
    await step("اختبار قناة التنبيهات", cb(A, "adm:ch:test:alerts"))

    # ── شحنة: العميل يطلب 20$ TRC20 → البطاقة تصل إلى قناة الإيداعات
    await dp.feed_update(bot, cb(U, "bal:topup")); await dp.feed_update(bot, cb(U, "bal:m:usdt_trc20")); await dp.feed_update(bot, cb(U, "bal:amt:20"))
    await dp.feed_update(bot, cb(U, "bal:paid:1")); sent.clear()
    await step("العميل يرسل الإثبات → البطاقة في قناة الإيداعات", msg(U, photo=True))
    t = await db.fetchrow("select id, admin_msg_ids from topups where id=1"); pairs = json.loads(t["admin_msg_ids"]) if isinstance(t["admin_msg_ids"], str) else t["admin_msg_ids"]
    print("   admin_msg_ids:", pairs)
    ch_id, m_id = pairs[0]
    await step("الأدمن يضغط ❌ رفض داخل القناة", cb(A, "adm:top:1:no", chat_id=ch_id, message_id=m_id))
    await step("رجوع (يعيد أزرار البطاقة)", cb(A, "adm:top:1:kb", chat_id=ch_id, message_id=m_id))
    await dp.feed_update(bot, cb(A, "adm:top:1:no", chat_id=ch_id, message_id=m_id)); sent.clear()
    await step("سبب آخر → يُكمل في الخاص", cb(A, "adm:top:1:no:custom", chat_id=ch_id, message_id=m_id))
    await step("الأدمن يكتب السبب في خاصّه", msg(A, "المبلغ لم يصل بعد — أعد الإرسال بعد التأكيد"))
    # شحنة ثانية: اعتماد بضغطة من القناة
    await dp.feed_update(bot, cb(U, "bal:topup")); await dp.feed_update(bot, cb(U, "bal:m:usdt_trc20")); await dp.feed_update(bot, cb(U, "bal:amt:20"))
    await dp.feed_update(bot, cb(U, "bal:paid:2")); await dp.feed_update(bot, msg(U, "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")); sent.clear()
    t = await db.fetchrow("select admin_msg_ids from topups where id=2"); pairs = json.loads(t["admin_msg_ids"]) if isinstance(t["admin_msg_ids"], str) else t["admin_msg_ids"]
    await step("✅ اعتماد من داخل القناة", cb(A, "adm:top:2:ok", chat_id=pairs[0][0], message_id=pairs[0][1]))
    print("   balance:", await db.fetchval("select balance_usd from users where tg_id=555"))

    # ── طلب: تجربة 13$ → البطاقة في قناة الطلبات
    await dp.feed_update(bot, cb(U, "meta:pkg:trial")); await dp.feed_update(bot, cb(U, "meta:plat:facebook")); await dp.feed_update(bot, cb(U, "meta:goal:messages"))
    await dp.feed_update(bot, cb(U, "meta:ctry:SY")); await dp.feed_update(bot, cb(U, "meta:prov_done")); await dp.feed_update(bot, cb(U, "meta:aud_done"))
    await dp.feed_update(bot, msg(U, "https://facebook.com/abaya/posts/1")); await dp.feed_update(bot, msg(U, "متجر عبايات في دمشق"))
    await dp.feed_update(bot, msg(U, photo=True)); await dp.feed_update(bot, cb(U, "meta:media_done")); await dp.feed_update(bot, cb(U, "meta:addon:copy:0"))
    await dp.feed_update(bot, msg(U, "0933123456")); sent.clear()
    await step("تأكيد الطلب → البطاقة في قناة الطلبات", cb(U, "meta:confirm"))
    o = await db.fetchrow("select admin_msg_ids from orders where id=1"); pairs = o["admin_msg_ids"] if not isinstance(o["admin_msg_ids"], str) else json.loads(o["admin_msg_ids"])
    print("   admin_msg_ids:", pairs)
    ch_id, m_id = pairs[0]
    await step("📎 ملفات العميل من القناة → تصل لخاص الأدمن", cb(A, "adm:ord:1:media", chat_id=ch_id, message_id=m_id))
    await step("🧪 نور قَبِل (من القناة)", cb(A, "adm:ord:1:sim:in_progress", chat_id=ch_id, message_id=m_id))
    await step("💬 مراسلة العميل من القناة → يكمل في الخاص", cb(A, "adm:ord:1:msg", chat_id=ch_id, message_id=m_id))
    await step("الأدمن يكتب الرسالة", msg(A, "تأكد أنك أدمن على الصفحة"))
    await step("↩️ استرداد من القناة → السبب في الخاص", cb(A, "adm:ord:1:refund", chat_id=ch_id, message_id=m_id))
    await step("الأدمن يكتب السبب", msg(A, "طلب العميل الإلغاء"))
    print("   balance after refund:", await db.fetchval("select balance_usd from users where tg_id=555"))

    # ── زر لا يخص أدمن (مستخدم عادي داخل القناة — لا يجب أن يعمل)
    await step("مستخدم غير أدمن يضغط زراً في القناة", cb(U, "adm:top:2:ok", chat_id=CH_TOP, message_id=5))

    # ── خطأ غير متوقع → قناة التنبيهات (نستدعي الميدلوير مباشرة)
    from app.bot.middlewares import ErrorsMiddleware
    async def boom(e, d): raise RuntimeError("test boom")
    await ErrorsMiddleware()(boom, msg(U, "x"), {"bot": bot}); show("خطأ غير متوقع → قناة التنبيهات")

    # ── إزالة البوت من قناة الطلبات → فصل تلقائي + رجوع للخاص
    await step("البوت أُزيل من قناة الطلبات", bot_added(CH_ORD, added=False))
    await step("القائمة بعد الفصل", cb(A, "adm:ch:menu"))
    await step("فصل قناة الإيداعات يدوياً", cb(A, "adm:ch:unbind:topups"))
    from aiogram.types import MessageOriginChannel
    fwd = Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=A, type="private"), from_user=user(A), text="بطاقة قديمة",
                  forward_origin=MessageOriginChannel(type="channel", date=datetime.now(), chat=Chat(id=CH_ORD, type="channel", title=CHANNELS[CH_ORD]), message_id=7))
    await step("احتياط: الأدمن يعيد توجيه رسالة من قناة الطلبات", Update(update_id=next(n), message=fwd))
    await step("يربطها كطلبات", cb(A, f"adm:ch:bind:orders:{CH_ORD}"))
    bal = await db.fetchval("select balance_usd from users where tg_id=555"); ls = await money_svc.ledger_sum(U)
    print(f"\nuser {U}: رصيد {bal} | دفتر {ls} | متطابق: {bal == ls}")
    print("channels cfg:", await db.fetchval("select value from settings where key='channels'"))
    await db.close_pool()

asyncio.run(main())
