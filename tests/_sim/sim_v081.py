"""محاكاة v0.8.1: 🎫 تذكرة + رد أدمن + 👤 بحث/تعديل رصيد + 📣 بث.
تشغيل: source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/sim_v081.py
"""
from __future__ import annotations

import asyncio, itertools, json, os, sys
from datetime import datetime
from decimal import Decimal

ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1", SUPPORT_USERNAME="support_demo", TZ="Asia/Damascus")

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery
from fakesession import FakeSession, sent
from app.db import pool as db
from app.db.repo import users, tickets
from app.main import build_dispatcher
from app.bot import keyboards as K
from app.services import money

mid = itertools.count(5000); upd = itertools.count(1)
A, U, U2 = 999, 555, 556

def usr(uid):
    return User(id=uid, is_bot=False, first_name="رأفت" if uid == A else ("سامر" if uid == U else "ليان"), username="admin" if uid == A else ("samer" if uid == U else "layan"))

def msg(uid, text):
    return Update(update_id=next(upd), message=Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=usr(uid), text=text))

def cb(uid, data):
    m = Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=User(id=1, is_bot=True, first_name="bot"), text="x")
    return Update(update_id=next(upd), callback_query=CallbackQuery(id=str(next(upd)), from_user=usr(uid), chat_instance="ci", message=m, data=data))

def dump(label):
    print(f"\n── {label}")
    for name, d in sent[-8:]:
        if name in ("SendMessage", "SendPhoto", "EditMessageText"):
            print(f"  {name} → {d.get('chat_id')}: {(d.get('text') or d.get('caption') or '')[:220].replace(chr(10), ' / ')}")
    sent.clear()

async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, ticket_messages, tickets, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM users")
    for uid in (A, U, U2):
        await users.upsert_user(uid, usr(uid).full_name, usr(uid).username); await users.accept_terms(uid)
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()

    await dp.feed_update(bot, msg(U, "/start")); await dp.feed_update(bot, cb(U, "nav:accept")); sent.clear()
    await dp.feed_update(bot, cb(U, "sup:menu")); await dp.feed_update(bot, cb(U, "sup:new")); dump("اختيار تذكرة")
    await dp.feed_update(bot, cb(U, "tck:open:general")); await dp.feed_update(bot, msg(U, "أحتاج مساعدة في طلب سابق")); dump("إنشاء التذكرة وإشعار الأدمن")
    t = (await tickets.list_for_user(U))[0]; assert t["status"] == "open"
    await dp.feed_update(bot, cb(A, "adm:tickets")); await dp.feed_update(bot, cb(A, f"adm:tck:{t['id']}:view")); dump("بطاقة الأدمن")
    await dp.feed_update(bot, cb(A, f"adm:tck:{t['id']}:reply")); await dp.feed_update(bot, msg(A, "أهلاً، كيف أساعدك؟")); dump("رد الأدمن")
    t = await tickets.get(t["id"]); assert t["status"] == "answered"

    await dp.feed_update(bot, cb(A, "adm:find")); await dp.feed_update(bot, msg(A, "@samer")); dump("بحث المستخدم")
    await dp.feed_update(bot, cb(A, f"adm:user:{U}:add")); await dp.feed_update(bot, msg(A, "5 تعويض اختبار")); dump("ملخص إضافة الرصيد")
    from aiogram.fsm.storage.base import StorageKey
    nonce = (await dp.storage.get_data(StorageKey(bot_id=bot.id, chat_id=A, user_id=A))).get("nonce")
    confirm_cd = f"adm:bal:confirm:{U}:{nonce}"   # v0.9.2: زر التأكيد يحمل رمزاً لمرة واحدة
    await dp.feed_update(bot, cb(A, confirm_cd)); dump("تأكيد الإضافة وإشعار العميل")
    assert await users.get_balance(U) == Decimal("5.00")
    assert await db.fetchval("SELECT count(*) FROM ledger WHERE user_id=$1 AND type='adjustment'", U) == 1

    await dp.feed_update(bot, cb(A, "adm:bc")); await dp.feed_update(bot, msg(A, "🎉 {name}، عرض تجريبي من ترويج بوت")); await dp.feed_update(bot, cb(A, "adm:bc:no_photo")); dump("اختيار جمهور البث")
    await dp.feed_update(bot, cb(A, "adm:bc:aud:new")); dump("تأكيد البث")
    await dp.feed_update(bot, cb(A, "adm:bc:confirm")); dump("نتيجة البث")
    print("balance:", await users.get_balance(U), "blocked_bot:", await db.fetchval("SELECT is_blocked_bot FROM users WHERE tg_id=$1", U), "tickets:", await tickets.count_open())
    await db.close_pool()

asyncio.run(main())
