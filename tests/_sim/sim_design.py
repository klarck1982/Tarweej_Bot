"""محاكاة 🎨 التصميم + 🛠️ المهام (v0.8.0):
D0 باقة (نص+صورة+ريل) ← D1..D8 مع صور وهوية ← تأكيد ← بطاقة الأدمن ← ▶️ بدأ ← 📤 تسليم (ملفان + تعليق) ← العميل ✏️ تعديل مجاني
← 📤 تسليم 2 ← ✏️ تعديل مدفوع (30%) بتأكيد ← 📤 تسليم 3 ← ✅ اعتماد ← طلب 2: نص فقط برصيد ناقص → مسودة → شحن → استئناف
← إلغاء العميل قبل البدء (استرداد) ← طلب 3: تصميم صورة «ما عندي مواد» بهوية محفوظة ← الأدمن ❌ تعذّر (استرداد)
← طلب 4: تسليم ثم اعتماد تلقائي بالمجدول ← تنبيهات المهلة (اقترب/متأخر) ← لوحة المهام ← Cpanel (إعدادات + إحصائيات).

تشغيل:  source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/sim_design.py
"""
import asyncio, os, sys, itertools, json, hmac, hashlib, time
from urllib.parse import urlencode
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1", SUPPORT_USERNAME="support_demo", TZ="Asia/Damascus")
from datetime import datetime
from decimal import Decimal
import aiohttp
from aiohttp import web
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery, PhotoSize, Video, Document
from fakesession import FakeSession, sent
from app.db import pool as db
from app.main import build_dispatcher
from app.services import money as money_svc, pricing as P, cpanel as CP, scheduler
from app.web import make_app
from app.web_cpanel import setup_cpanel
from app.db.repo import users, orders as orders_repo

mid = itertools.count(9000); n = itertools.count(1)
A = 999; U = 555
def user(uid): return User(id=uid, is_bot=False, first_name="رأفت" if uid == A else "سامر", username="rafat" if uid == A else "samer")
def _m(uid, **kw): return Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=user(uid), **kw)
def msg(uid, text): return Update(update_id=next(n), message=_m(uid, text=text))
def photo(uid, caption=None, fid="AgACphoto", group=None):
    ph = [PhotoSize(file_id=fid, file_unique_id="u1", width=800, height=600, file_size=1000)]
    return Update(update_id=next(n), message=_m(uid, photo=ph, caption=caption, media_group_id=group))
def video(uid, fid="BAACvideo"):
    return Update(update_id=next(n), message=_m(uid, video=Video(file_id=fid, file_unique_id="v1", width=720, height=1280, duration=12)))
def doc(uid, fid="BQACdoc", mime="image/png", caption=None):
    return Update(update_id=next(n), message=_m(uid, document=Document(file_id=fid, file_unique_id="d1", file_name="design.png", mime_type=mime), caption=caption))
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
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + b['text'] + (" ↗" if b.get("url") else "") for b in r))
        elif name in ("SendPhoto", "SendMediaGroup", "SendVideo", "SendDocument", "SendAudio"): print(f"  [{name}→{d.get('chat_id')}] {d.get('caption') or ''} {('x'+str(len(d['media']))) if d.get('media') else ''}")
        elif name == "AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:200]}")
        elif name == "EditMessageReplyMarkup": print("  [buttons removed]")
    sent.clear()

def init_data(uid, age=0):
    u = json.dumps({"id": uid, "first_name": "رأفت", "username": "rafat"}, ensure_ascii=False)
    params = {"auth_date": str(int(time.time()) - age), "query_id": "AAE", "user": u}
    dcs = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", b"123456:TESTTOKEN", hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)

async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM users WHERE tg_id IN (555,999)")
    await db.execute("DELETE FROM settings WHERE key IN ('channels','services','pricing','design_approve_hours','design_revision_pct')")
    from app.db.repo import settings as srepo; srepo.invalidate(); await P.refresh(); await CP.refresh_runtime()
    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()
    app = make_app(); setup_cpanel(app, bot)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", 18098).start()
    base = "http://127.0.0.1:18098"
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

        print("\n══ 1) الباقة نص+صورة+ريل (24$) — D0→D9")
        await step("🎨 القائمة", cb(U, "nav:design"))
        await step("📦 الباقات", cb(U, "add:svc:bundles"), full=True)
        await step("اختيار باقة 2 → D1", cb(U, "ds:bundle:1"), full=True)
        await step("D1 مطعم → D2", cb(U, "ds:biz:restaurant"))
        await step("رسالة قصيرة (رفض)", msg(U, "خصم"))
        await step("D2 الرسالة → D3 مواد", msg(U, "افتتاح فرعنا الجديد بالمزة — خصم 30% على كل الأصناف حتى الجمعة"), full=True)
        await step("✅ تم بلا مواد (تحذير)", cb(U, "ds:media:done"))
        await step("صورة 1", photo(U, fid="P1"))
        await step("ألبوم 2+3 (رد واحد)", photo(U, fid="P2", group="g1")); await step("ألبوم تابع", photo(U, fid="P3", group="g1"))
        await step("فيديو", video(U, fid="V1"))
        await step("نص أثناء المواد", msg(U, "هاي كل شي"))
        await step("✅ تم (يكفي؟ 3 صور + 1 فيديو → لا: تحذير مرة واحدة)", cb(U, "ds:media:done"))
        await step("صورتان إضافيتان", photo(U, fid="P4")); await step("…", photo(U, fid="P5"))
        await step("✅ تم → D4 الهوية", cb(U, "ds:media:done"), full=True)
        await step("لوغو + ألوان بالتعليق", doc(U, fid="LOGO1", caption="أحمر وذهبي"))
        await step("✅ تم واحفظ → D5", cb(U, "ds:brand:done"))
        await step("لهجة خليجي", cb(U, "ds:lang:gulf"))
        await step("نبرة عرض قوي", cb(U, "ds:tone:hot"))
        await step("تابع → D6 إضافات", cb(U, "ds:lang:done"), full=True)
        await step("تعليق صوتي +5$", cb(U, "ds:extra:voiceover"))
        await step("موسيقى", cb(U, "ds:extra:music"))
        await step("تابع → D7", cb(U, "ds:extras:done"))
        await step("ملاحظات → D8 الملخص", msg(U, "المقاس مربع للإنستغرام ولا تستخدموا اللون الأزرق"), full=True)
        await step("✏️ تعديل", cb(U, "ds:edit"))
        await step("تعديل اللغة", cb(U, "ds:back:lang"))
        await step("شامي", cb(U, "ds:lang:levant"))
        await step("تابع → الملخص مباشرة", cb(U, "ds:lang:done"), full=True)
        await step("✅ تأكيد ودفع 29$", cb(U, "ds:confirm"), full=True)
        o1 = await orders_repo.get(1); print(f"  ORD-1: status={o1['status']} kind={o1['kind']} price={o1['price_usd']} (24+5) cost={o1['cost_usd']} due_at-paid={(o1['due_at']-o1['paid_at'])} media={len(await orders_repo.media(1))} (5 صور+1 فيديو+لوغو) balance={await users.get_balance(U)}")
        bk = await db.fetchval("SELECT brand_kit FROM users WHERE tg_id=555"); print("  brand_kit:", bk)

        print("\n══ 2) الأدمن: البطاقة → ▶️ بدأ → 📤 تسليم")
        await step("العميل: عرض الطلب", cb(U, "ord:view:1"), full=True)
        await step("الأدمن: لوحة", msg(A, "/admin"))
        await step("الأدمن: 🛠️ المهام", cb(A, "adm:tasks"))
        await step("الأدمن: البطاقة", cb(A, "adm:ord:1:view"), full=True)
        await step("العميل يلغي قبل البدء؟ (شاشة تأكيد)", cb(U, "ds:cancel_order:1"))
        await step("الأدمن ▶️ بدأت العمل", cb(A, "adm:ds:1:start"), full=True)
        await step("العميل: نعم ألغِ (متأخر — بدأ العمل)", cb(U, "ds:cancel_yes:1"))
        await step("الأدمن 📤 تسليم", cb(A, "adm:ds:1:deliver"), full=True)
        await step("✅ أرسل بلا ملفات (رفض)", cb(A, "adm:ds:1:send"))
        await step("ملف PNG مع تعليق", doc(A, fid="OUT1", caption="النسخة الأولى — المقاس مربع كما طلبت"))
        await step("فيديو الريل", video(A, fid="OUTV1"))
        await step("✅ أرسل للعميل", cb(A, "adm:ds:1:send"), full=True)
        o1 = await orders_repo.get(1); print(f"  status={o1['status']} deliveries={len(o1['delivery'])} approve_by-delivered={(o1['approve_by']-o1['delivered_at'])}")

        print("\n══ 3) العميل: ✏️ تعديل مجاني → تسليم 2 → ✏️ تعديل مدفوع (30% من 29 = 8.70) → تسليم 3 → ✅ اعتماد")
        await step("✏️ طلب تعديل (مجاني)", cb(U, "ds:revise:1"))
        await step("نص التعديل", msg(U, "غيّروا لون الخلفية للأبيض وكبّروا السعر"), full=True)
        o1 = await orders_repo.get(1); print(f"  status={o1['status']} revision_count={o1['revision_count']} price={o1['price_usd']} due_at-now≈{(o1['due_at']-datetime.now(o1['due_at'].tzinfo)).total_seconds()/3600:.1f}h balance={await users.get_balance(U)}")
        await step("الأدمن: بطاقة بعد التعديل", cb(A, "adm:ord:1:view"), full=True)
        await step("الأدمن 📤 تسليم النسخة المعدّلة", cb(A, "adm:ds:1:deliver"))
        await step("ملف", doc(A, fid="OUT2"))
        await step("تعليق منفصل", msg(A, "عدّلنا الخلفية والسعر"))
        await step("✅ أرسل", cb(A, "adm:ds:1:send"), full=True)
        await step("✏️ تعديل ثانٍ (مدفوع)", cb(U, "ds:revise:1"), full=True)
        await step("نص التعديل 2", msg(U, "بدي نسخة ثانية بالعربي الفصيح"), full=True)
        await step("✅ أدفع وأرسل", cb(U, "ds:revise_pay:1"), full=True)
        o1 = await orders_repo.get(1); print(f"  status={o1['status']} revision_count={o1['revision_count']} price={o1['price_usd']} (29+8.70) balance={await users.get_balance(U)}")
        await step("الأدمن 📤 تسليم 3", cb(A, "adm:ds:1:deliver"))
        await step("صورة", photo(A, fid="OUT3"))
        await step("✅ أرسل", cb(A, "adm:ds:1:send"))
        await step("العميل 📥 أعد إرسال الملفات", cb(U, "ds:files:1"))
        await step("العميل ✅ اعتمده", cb(U, "ds:approve:1"), full=True)
        o1 = await orders_repo.get(1); print(f"  status={o1['status']} completed_at={o1['completed_at'] is not None}")
        await step("الأدمن: بطاقة مكتملة", cb(A, "adm:ord:1:view"), full=True)
        await step("الأدمن 📥 ما سلّمته", cb(A, "adm:ds:1:files"))

        print("\n══ 4) طلب 2: نص فقط (5$) برصيد ناقص → مسودة → شحن → استئناف → إلغاء قبل البدء")
        await db.execute("UPDATE users SET balance_usd = 2 WHERE tg_id = 555")
        await step("✍️ نص إعلاني", cb(U, "add:svc:copy"), full=True)
        await step("متجر", cb(U, "ds:biz:shop"))
        await step("الرسالة → D4 (لا مواد للنص)", msg(U, "تشكيلة الشتاء وصلت — أسعار تبدأ من 50 ألف"), full=True)
        await step("استخدم هويتي المحفوظة → D5", cb(U, "ds:brand:saved"))
        await step("تابع → D7 (لا إضافات فيديو)", cb(U, "ds:lang:done"))
        await step("تخطّي → D8 ملخص ناقص", cb(U, "ds:notes:skip"), full=True)
        d = await orders_repo.get_awaiting(U); print(f"  draft: id={d['id']} kind={d['kind']} price={d['price_usd']} status={d['status']}")
        await step("تأكيد رغم النقص (سباق)", cb(U, "ds:confirm"))
        await money_svc.credit(U, Decimal("10"), "topup", note="sim")
        await step("استئناف", cb(U, "ord:resume"), full=True)
        await step("تأكيد", cb(U, "ds:confirm"))
        o2 = await orders_repo.get(d["id"]); print(f"  ORD-{o2['id']} status={o2['status']} price={o2['price_usd']} due={(o2['due_at']-o2['paid_at'])} balance={await users.get_balance(U)}")
        await step("العميل: إلغاء", cb(U, f"ds:cancel_order:{o2['id']}"))
        await step("نعم", cb(U, f"ds:cancel_yes:{o2['id']}"), full=True)
        o2 = await orders_repo.get(o2["id"]); print(f"  status={o2['status']} refunded={o2['refunded_usd']} balance={await users.get_balance(U)}")

        print("\n══ 5) طلب 3: تصميم صورة بلا مواد + هوية محفوظة → الأدمن ❌ تعذّر (استرداد)")
        await step("🖼️ تصميم صورة", cb(U, "add:svc:design"))
        await step("عيادة", cb(U, "ds:biz:clinic"))
        await step("الرسالة", msg(U, "كشف مجاني للأسنان يوم السبت — احجز الآن"))
        await step("ما عندي مواد", cb(U, "ds:media:none"))
        await step("تخطّي الهوية", cb(U, "ds:brand:skip"))
        await step("فصحى + رسمي", cb(U, "ds:lang:msa")); await step("…", cb(U, "ds:tone:formal"))
        await step("تابع", cb(U, "ds:lang:done"))
        await step("ملاحظات طويلة (رفض)", msg(U, "ن" * 301))
        await step("تخطّي", cb(U, "ds:notes:skip"), full=True)
        await step("تأكيد 8$", cb(U, "ds:confirm"))
        o3 = await orders_repo.get(3); print(f"  ORD-3 status={o3['status']} price={o3['price_usd']} no_media={o3['spec']['no_media']} balance={await users.get_balance(U)}")
        await step("الأدمن ❌ تعذّر", cb(A, "adm:ds:3:reject"))
        await step("السبب", msg(A, "المطلوب يحتاج تصوير احترافي لا نوفره حالياً"), full=True)
        o3 = await orders_repo.get(3); print(f"  status={o3['status']} refunded={o3['refunded_usd']} balance={await users.get_balance(U)}")

        print("\n══ 6) طلب 4: مونتاج → تسليم → اعتماد تلقائي بالمجدول + تنبيهات المهلة")
        await money_svc.credit(U, Decimal("30"), "topup", note="sim")
        await step("🎞️ مونتاج", cb(U, "add:svc:montage"))
        await step("غير ذلك", cb(U, "ds:biz:other"))
        await step("الرسالة", msg(U, "فيديو تعريفي بخدمات شركتنا للنقل"))
        await step("✅ تم بلا فيديو (تحذير)", cb(U, "ds:media:done"))
        await step("فيديو", video(U, fid="V9"))
        await step("✅ تم", cb(U, "ds:media:done"))
        await step("تخطّي الهوية", cb(U, "ds:brand:skip"))
        await step("تابع", cb(U, "ds:lang:done"))
        await step("ترجمة", cb(U, "ds:extra:subs"))
        await step("تابع", cb(U, "ds:extras:done"))
        await step("تخطّي", cb(U, "ds:notes:skip"))
        await step("تأكيد 25$", cb(U, "ds:confirm"))
        o4 = await orders_repo.get(4); print(f"  ORD-4 status={o4['status']} price={o4['price_usd']} due={(o4['due_at']-o4['paid_at'])} extras={o4['spec']['extras']}")
        # تنبيه «اقترب الموعد»: نجعل المهلة 72 ساعة بدأت قبل 60 ساعة (بقي 12 = 17%)
        await db.execute("UPDATE orders SET paid_at = now() - interval '60 hours', due_at = now() + interval '12 hours' WHERE id = 4")
        await scheduler.tick(bot); show("دورة المجدول: ⏰ اقترب الموعد")
        await db.execute("UPDATE orders SET due_at = now() - interval '1 hour' WHERE id = 4")
        await scheduler.tick(bot); show("دورة المجدول: 🔴 متأخر")
        await scheduler.tick(bot); print("  (دورة ثالثة بلا تكرار:", len([1 for nm, _ in sent if nm == "SendMessage"]), "رسائل)"); sent.clear()
        await step("الأدمن: لوحة (متأخر)", msg(A, "/admin"))
        await step("الأدمن: المهام", cb(A, "adm:tasks"), full=True)
        await step("الأدمن 📤 تسليم", cb(A, "adm:ds:4:deliver"))
        await step("فيديو", video(A, fid="OUTV4"))
        await step("✅ أرسل", cb(A, "adm:ds:4:send"))
        await db.execute("UPDATE orders SET approve_by = now() - interval '1 minute' WHERE id = 4")
        await scheduler.tick(bot); show("دورة المجدول: ✅ اعتماد تلقائي", full=True)
        o4 = await orders_repo.get(4); print(f"  status={o4['status']}")
        await step("العميل: طلباتي", msg(U, "/orders"))
        await step("الأدمن: المهام (فارغة)", cb(A, "adm:tasks"))

        print("\n══ 7) Cpanel: إعدادات التصميم + الإحصائيات")
        snap = await post("snapshot"); g = dict(snap["general"]); print("  defaults:", g.get("design_approve_hours"), g.get("design_revision_pct"))
        await post("save/general", {**g, "design_approve_hours": 0}, expect=400)
        await post("save/general", {**g, "design_revision_pct": "150"}, expect=400)
        r = await post("save/general", {**g, "design_approve_hours": 24, "design_revision_pct": "12.5"})
        g = r.get("general") or {}; print("  saved:", g.get("design_approve_hours"), g.get("design_revision_pct"), "| changed:", r.get("changed"))
        from app.services import design as DS
        print("  runtime: approve_hours =", DS.approve_hours(), "| revision_pct =", DS.revision_pct(), "| label =", DS.pct_label())
        await db.execute("UPDATE orders SET status='delivered', revision_count=1, approve_by=now()+interval '1 day' WHERE id=4")
        await step("تعديل مدفوع بنسبة 12.5% من 25 = 3.13", cb(U, "ds:revise:4"))
        await db.execute("UPDATE orders SET status='completed', approve_by=NULL WHERE id=4")
        st = await post("stats", {"period": "7d"}); print(f"  stats: orders={st['orders']} revenue={st['revenue']} profit={st['profit']} design_working={st['design_working']} late={st['design_late']} top={st['top_services']}")
        # الحسابات: 29 + 7.20 (ORD-1) + 25 (ORD-4) = 61.20 إيراد؛ ORD-2/3 مستردة
        print("  ledger sum(order_charge) =", await db.fetchval("SELECT coalesce(sum(amount_usd),0) FROM ledger WHERE type='order_charge'"),
              "| refunds =", await db.fetchval("SELECT coalesce(sum(amount_usd),0) FROM ledger WHERE type='refund'"),
              "| balance =", await users.get_balance(U))
        await step("🔒 addons مقفول", cb(A, "adm:svc:addons"))
        await step("العميل يضغط نص إعلاني", cb(U, "add:svc:copy"))
        await step("الباقات", cb(U, "add:svc:bundles"))
    await runner.cleanup(); await db.close_pool()

asyncio.run(main())
