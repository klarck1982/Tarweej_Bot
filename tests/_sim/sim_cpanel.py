"""محاكاة Cpanel: خادم HTTP حقيقي + قاعدة محلية.
1) بوابة: بلا توقيع → 401، غير أدمن → 403، أدمن → snapshot.
2) حفظ أسعار (مضاعف تيليغرام 1.40 + إخفاء باقة انطلاقة متجر) → البوت يعرض الأسعار الجديدة فوراً.
3) حفظ عام (دعم، قناة عروض، وضع صيانة) → القائمة الرئيسية تتغير، ومحاولة طلب تُرفض بتنبيه الصيانة.
4) حفظ دفع (إطفاء شام كاش ليرة) + خدمات (إيقاف Meta) → شاشة الشحن والقائمة تتغيران.
5) قيم خاطئة → 400 برسالة عربية؛ سجل التغييرات يمتلئ.
تشغيل:  source /tmp/pg_env.sh && SIM_ROOT=/home/user/promo-bot python3 tests/_sim/sim_cpanel.py
"""
import asyncio, os, sys, itertools, json, hmac, hashlib, time
from urllib.parse import urlencode
ROOT = os.environ.get("SIM_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests", "_sim"))
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
                  ADMIN_IDS="999", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1", SUPPORT_USERNAME="support_env", PORT="18099")
from datetime import datetime
from decimal import Decimal
import aiohttp
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, Chat, User, CallbackQuery
from aiohttp import web
from fakesession import FakeSession, sent
from app.db import pool as db
from app.main import build_dispatcher
from app.services import money as money_svc, pricing as P, cpanel as CP
from app.web import make_app
from app.web_cpanel import setup_cpanel

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
            print(f"  [{name}→{d.get('chat_id')}] {txt[: (900 if full else 200)].replace(chr(10), ' / ')}")
            for r in rows: print("     " + " | ".join(COL.get(b.get('style'), '⬜') + (b['text'] + (" [webapp]" if b.get("web_app") else "") + (" [url]" if b.get("url") else "")) for b in r))
        elif name == "AnswerCallbackQuery" and d.get("text"): print(f"  [toast] {d['text'][:160]}")
    sent.clear()

def init_data(uid, name="أدمن", age=0):
    params = {"auth_date": str(int(time.time()) - age), "query_id": "AAH", "user": json.dumps({"id": uid, "first_name": name}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", b"123456:TESTTOKEN", hashlib.sha256).digest()
    return urlencode({**params, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})

async def main():
    await db.init_pool(os.environ["DATABASE_URL"]); print("migrations:", await db.run_migrations())
    await db.execute("TRUNCATE fsm_state, events, ledger, topups, order_media, orders RESTART IDENTITY CASCADE")
    await db.execute("DELETE FROM users WHERE tg_id IN (555,999)")
    await db.execute("DELETE FROM settings WHERE key IN ('channels','pricing','support_username','updates_channel','maintenance','maintenance_msg','disabled_style','max_topup_usd','topup_presets_usd','services','payment_methods')")
    await db.execute("UPDATE settings SET value = jsonb_set(value, '{usdt_trc20,address}', '\"TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE\"') WHERE key='payment_methods'")
    await db.execute("""INSERT INTO settings(key,value) VALUES ('payment_methods','{"usdt_trc20":{"address":"TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"},"shamcash_syp":{"address":"0999"},"shamcash_usd":{"address":"0888","holder":"رأفت"}}'::jsonb) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""")
    await db.execute("""INSERT INTO settings(key,value) VALUES ('syp_per_usd','"11000"'::jsonb) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""")
    from app.db.repo import settings as srepo; srepo.invalidate()
    await P.refresh(); await CP.refresh_runtime()

    bot = Bot("123456:TESTTOKEN", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML")); dp = build_dispatcher()
    app = make_app(); setup_cpanel(app, bot)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", 18099).start()
    base = "http://127.0.0.1:18099"
    async def step(l, u, full=False): await dp.feed_update(bot, u); show(l, full)
    for uid in (U, A):
        await dp.feed_update(bot, msg(uid, "/start")); await dp.feed_update(bot, cb(uid, "nav:accept"))
    sent.clear()
    await money_svc.credit(U, Decimal("40"), "topup", note="sim")
    # طلب مدفوع للإحصائيات
    await db.execute("""INSERT INTO orders(user_id,kind,status,spec,price_usd,cost_usd,paid_at) VALUES (555,'tg_ads','completed','{"kind":"tg_ads","budget":"25"}',33.75,25,now())""")

    async with aiohttp.ClientSession() as http:
        async def post(path, body=None, uid=A, raw_init=None, expect=200):
            headers = {"X-Telegram-Init-Data": raw_init if raw_init is not None else init_data(uid)}
            async with http.post(f"{base}/cpanel/api/{path}", json=body or {}, headers=headers) as r:
                data = await r.json(); tag = "✅" if r.status == expect else "❌"
                print(f"  {tag} POST {path} → {r.status}" + (f" | {data.get('message')}" if data.get('message') else ""))
                return data
        print("\n── 1) البوابة")
        async with http.get(f"{base}/cpanel") as r:
            html = await r.text(); print(f"  GET /cpanel → {r.status}, html {len(html)} bytes, contains tabs: {'tab-pricing' in html}")
        await post("snapshot", raw_init="", expect=401)
        await post("snapshot", raw_init=init_data(A, age=5000), expect=401)
        await post("snapshot", uid=U, expect=403)
        snap = await post("snapshot")
        st = snap["stats"]; print(f"  snapshot: v{snap['version']} users={st['users_total']} orders={st['orders']} revenue={st['revenue']} profit={st['profit']} margin={st['margin_pct']}% nour={st['nour_balance']} services={snap['services']} audit={len(snap['audit'])}")
        print(f"  pricing.tg_ads = {snap['pricing']['tg_ads']} | general.support = {snap['general']['support_username']!r} env={snap['general_env']}")

        print("\n── 2) حفظ الأسعار: تيليغرام ×1.40 + إخفاء انطلاقة متجر + باقة جديدة")
        pr = snap["pricing"]; pr["tg_ads"]["mult"] = "1.40"; pr["tg_ads"]["presets"] = [10, 20, 30, 50, 100, 200]
        pr["bundle"]["enabled"] = False
        pr["packages"].append({"code": "mega", "title": "ضخمة", "emoji": "🏆", "daily": "10", "days": 14, "blurb": "للحملات الكبيرة", "enabled": True})
        res = await post("save/pricing", pr); print("  changed:", res.get("changed"))
        print(f"  P.TG_ADS_MULT={P.TG_ADS_MULT} presets={P.TG_ADS_PRESETS} bundle_enabled={P.bundle_enabled()} pkgs={[p.code for p in P.META_PACKAGES]}")
        await step("البوت: شاشة تيليغرام (سعر جديد)", cb(U, "nav:tg"))
        await step("البوت: TA1 أزرار الميزانية (6 أرقام + اكتب مبلغاً)", cb(U, "tga:start"))
        await step("زر اكتب مبلغاً آخر", cb(U, "tga:budget:type"))
        await step("مبلغ 200 → 280$", msg(U, "200"))
        await step("إلغاء", cb(U, "tga:cancel"))
        await step("البوت: باقات Meta (بلا انطلاقة متجر + باقة ضخمة)", cb(U, "nav:meta"))
        await step("البوت: ℹ️ الأسعار", cb(U, "info:ads"), full=True)

        print("\n── 3) قيم خاطئة")
        bad = json.loads(json.dumps(pr)); bad["meta"]["mult"] = "1.05"; await post("save/pricing", bad, expect=400)
        bad = json.loads(json.dumps(pr)); bad["tg_ads"]["presets"] = [3]; await post("save/pricing", bad, expect=400)
        await post("save/general", {"support_username": "ab", "topup_presets_usd": [10]}, expect=400)
        await post("save/general", {"support_username": "help_desk", "updates_channel": "example.com/x", "topup_presets_usd": [10]}, expect=400)

        print("\n── 4) حفظ عام: دعم + قناة عروض + صيانة + إخفاء المعطّل + حدود شحن")
        g = snap["general"]; g.update(support_username="@tarweej_support", updates_channel="@tarweej_offers", maintenance=True,
                                      maintenance_msg="نحدّث النظام — نعود خلال ساعة 🙏", disabled_style="hide", min_topup_usd="10", max_topup_usd="500", topup_presets_usd=[10, 25, 50])
        res = await post("save/general", g); print("  changed:", res.get("changed"))
        print(f"  rt: support={CP.rt('support_username')} channel={CP.rt('updates_channel')} maint={CP.rt('maintenance')} style={CP.rt('disabled_style')}")
        await step("البوت: القائمة الرئيسية (زر قناة العروض)", msg(U, "/start"))
        await step("البوت: الدعم (تواصل مباشر)", cb(U, "sup:menu"))
        await step("البوت: محاولة بدء طلب في الصيانة", cb(U, "tga:start"))
        await step("البوت: شحن → المبالغ (حدود جديدة)", cb(U, "bal:topup"))
        await step("USDT", cb(U, "bal:m:usdt_trc20"))

        print("\n── 5) الدفع + الخدمات")
        pm = snap["payments"]; pm["shamcash_syp"]["enabled"] = False; pm["usdt_bep20"]["address"] = "0x8f3B000000000000000000000000000000c41A"
        res = await post("save/payments", pm); print("  changed:", res.get("changed"))
        res = await post("save/services", {"meta": False, "tg_post": True}); print("  changed:", res.get("changed"), "(tg_post مقفول → يُتجاهل)")
        await step("البوت: طرق الشحن (بلا ليرة، مع BEP20)", cb(U, "bal:topup"))
        await step("البوت: القائمة (Meta مخفي لأن style=hide)", msg(U, "/start"))
        g["maintenance"] = False; g["disabled_style"] = "lock"; await post("save/general", g)
        await post("save/services", {"meta": True})
        await step("البوت: القائمة بعد إعادة التشغيل", msg(U, "/start"))

        print("\n── 6) الإحصائيات + السجل + زر Cpanel")
        st = await post("stats", {"period": "30d"}); print(f"  30d: orders={st['orders']} revenue={st['revenue']} chart_days={len(st['chart'])} top={st['top_services']}")
        snap2 = await post("snapshot"); print(f"  audit entries: {len(snap2['audit'])} — first: {snap2['audit'][0]['section']}.{snap2['audit'][0]['key']} {snap2['audit'][0]['before']} → {snap2['audit'][0]['after']}")
        await post("channel_test", {"kind": "orders"}, expect=400)
    await step("الأدمن: /cpanel (محلياً بلا https)", msg(A, "/cpanel"))
    await step("الأدمن: /admin", msg(A, "/admin"))
    from app.config import settings as _cfg
    object.__setattr__(_cfg, "public_url", "https://taxweej-bot.onrender.com")
    await step("الأدمن: /admin مع https (زر Cpanel)", msg(A, "/admin"))
    await step("الأدمن: /cpanel مع https", msg(A, "/cpanel"))
    await runner.cleanup()
    await db.execute("DELETE FROM settings WHERE key IN ('pricing','support_username','updates_channel','maintenance','maintenance_msg','disabled_style','max_topup_usd','topup_presets_usd','min_topup_usd')")
    await db.close_pool()

asyncio.run(main())
