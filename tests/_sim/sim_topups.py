import asyncio, os, sys, itertools
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL="postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable", ADMIN_IDS="999", MODE="polling", SUPPORT_USERNAME="support_demo")
sys.path.insert(0,"/home/user/promo-bot"); sys.path.insert(0,"/tmp")
from datetime import datetime
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery, PhotoSize
from fakesession import FakeSession, sent
from app.db import pool as db
from app.main import build_dispatcher
from app.services import money as money_svc
mid=itertools.count(9000); n=itertools.count(1)
def user(uid): return User(id=uid,is_bot=False,first_name="سامر",username="samer")
def msg(uid,text=None,photo=False):
    kw=dict(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid,type="private"), from_user=user(uid))
    if photo: kw["photo"]=[PhotoSize(file_id="AgACAgQAAxkBAAI_photo", file_unique_id="u1", width=800, height=600)]
    else: kw["text"]=text
    return Update(update_id=next(n), message=Message(**kw))
def cb(uid,data):
    m=Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid,type="private"), from_user=User(id=1,is_bot=True,first_name="b"), text="x")
    return Update(update_id=next(n), callback_query=CallbackQuery(id=str(next(n)), from_user=user(uid), chat_instance="ci", message=m, data=data))
COL={"success":"🟩","danger":"🟥","primary":"🟦"}
def show(label):
    print(f"\n── {label}")
    for name,d in sent:
        if name in ("SendMessage","EditMessageText","SendPhoto","EditMessageCaption"):
            kb=d.get("reply_markup") or {}; rows=kb.get("inline_keyboard") or kb.get("keyboard") or []
            print(f"  [{name}→{d.get('chat_id')}] {(d.get('text') or d.get('caption') or '')[:260].replace(chr(10),' / ')}")
            for r in rows: print("     "+" | ".join(COL.get(b.get('style'),'⬜')+b['text'] for b in r))
        elif name=="AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:90]}")
    sent.clear()
async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE; DELETE FROM users WHERE tg_id IN (555,999)")
    # نبدأ من حالة "المستخدم أدخل عنوان TRC20 سابقاً" (كما في الإنتاج) ثم طُبّق 003
    print("payment_methods keys:", list((await db.fetchval("select value from settings where key='payment_methods'")) and __import__('json').loads(await db.fetchval("select value from settings where key='payment_methods'")) or {}))
    bot=Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp=build_dispatcher()
    async def step(l,u): await dp.feed_update(bot,u); show(l)
    U=555; A=999
    await dp.feed_update(bot, msg(U,"/start")); await dp.feed_update(bot, cb(U,"nav:accept")); sent.clear()
    await step("القائمة الرئيسية (عرض كامل)", msg(U,"🏠 القائمة"))
    await step("شحن — قبل أي إعداد شام كاش (TRC20 موجود من قبل)", cb(U,"bal:topup"))
    # الأدمن: إعداد شام كاش
    await dp.feed_update(bot, msg(A,"/start")); await dp.feed_update(bot, cb(A,"nav:accept")); sent.clear()
    await step("الأدمن: طرق الدفع", cb(A,"adm:wallets"))
    await step("الأدمن: شام كاش دولار", cb(A,"adm:wal:shamcash_usd"))
    await step("الأدمن: تعديل رقم الحساب", cb(A,"adm:wal:shamcash_usd:edit"))
    await step("الأدمن: يرسل رقم بأرقام عربية", msg(A,"٠٩٨٨١٢٣٤٥٦"))
    await step("الأدمن: يرسل الاسم", msg(A,"رأفت  أحمد"))
    await step("الأدمن: شام كاش ليرة → رقم", cb(A,"adm:wal:shamcash_syp:edit"))
    await dp.feed_update(bot, msg(A,"0988123456")); await dp.feed_update(bot, msg(A,"رأفت أحمد")); sent.clear()
    await step("الأدمن: سعر الصرف", cb(A,"adm:rate"))
    await step("الأدمن: سعر خاطئ", msg(A,"abc"))
    await step("الأدمن: 11,250", msg(A,"11,250"))
    # العميل: شحن بالليرة
    await step("العميل: شحن — الطرق الآن", cb(U,"bal:topup"))
    await step("العميل: يختار ليرة", cb(U,"bal:m:shamcash_syp"))
    await step("العميل: 10$", cb(U,"bal:amt:10"))
    await step("العميل: حوّلت", cb(U,"bal:paid:1"))
    await step("العميل: TxID طويل (غير مناسب لشام كاش؟ يُقبل كرقم عملية إن كان ≤40)", msg(U,"7f3a9c1b2d4e5f60718293a4b5c6d7e8f9012345"))
    await step("العميل: رقم عملية قصير", msg(U,"48213377"))
    await step("الأدمن: يعتمد", cb(A,"adm:top:1:ok"))
    # شحن دولار شام كاش بصورة
    await dp.feed_update(bot, cb(U,"bal:topup")); await dp.feed_update(bot, cb(U,"bal:m:shamcash_usd")); sent.clear()
    await step("العميل: يكتب ٢٠ (دولار شام كاش)", msg(U,"٢٠"))
    await dp.feed_update(bot, cb(U,"bal:paid:2")); sent.clear()
    await step("العميل: صورة", msg(U,photo=True))
    await step("الأدمن: اعتماد بمبلغ مختلف", cb(A,"adm:top:2:adj")); await step("الأدمن: 19", msg(A,"19"))
    await step("العميل: رصيدي", cb(U,"bal:menu"))
    bal=await db.fetchval("select balance_usd from users where tg_id=555"); ls=await money_svc.ledger_sum(555)
    print(f"\nرصيد: {bal} | دفتر: {ls} | متطابق: {bal==ls}")
    print("topups:", [(r['id'],r['method'],str(r['amount_usd']),str(r['amount_local']),str(r['rate']),r['status']) for r in await db.fetch("select * from topups order by id")])
    await db.close_pool()
asyncio.run(main())
