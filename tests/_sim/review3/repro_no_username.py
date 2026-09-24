"""عميل بلا @username يطلب إعلان Meta ← ماذا يحدث مع نور الحقيقي (خادم وهمي يطبّق الوثائق: telegram_username إلزامي)؟"""
import asyncio, os, sys, random
from decimal import Decimal
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("ADMIN_IDS", "1")
os.environ["DATABASE_URL"] = DSN = "postgresql://postgres@127.0.0.1:5433/promobot"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
from aiohttp import web
CODE = sys.argv[1] if len(sys.argv) > 1 else "missing_field"
SENT = []

def app():
    async def campaigns(req):
        body = await req.json(); SENT.append(body.get("telegram_username"))
        for f in ("whatsapp_number", "telegram_username", "duration_days"):
            if not body.get(f):
                return web.json_response({"success": False, "code": CODE, "message": f"{f} is required"}, status=400)
        return web.json_response({"success": True, "data": {"id": 9001, "charged": 11.0}}, status=201)
    async def listing(req):
        return web.json_response({"success": True, "data": []})
    a = web.Application(); a.router.add_post("/wp-json/mca/v1/campaigns", campaigns)
    a.router.add_get("/wp-json/mca/v1/campaigns", listing); return a

async def main():
    r = web.AppRunner(app()); await r.setup(); await web.TCPSite(r, "127.0.0.1", 18096).start()
    from app.db import pool as db
    await db.init_pool(DSN); await db.run_migrations()
    from app.db.repo import users, settings as S
    from app.services import money as M, orders as O, nour
    nour.BASE_URL = "http://127.0.0.1:18096/wp-json/mca/v1"
    live = nour.NourClient("mca_live_ok"); nour.client = lambda: live; nour.is_dry_run = lambda: False
    fb = sys.argv[2] if len(sys.argv) > 2 else ""
    await S.set_("admin_fallback_username", fb)
    uid = random.randint(10**9, 2*10**9)
    await users.upsert_user(uid, "بلا معرف", None); await M.credit(uid, Decimal("100"), "topup")
    spec = {"kind": "meta_campaign", "pkg": "p1", "daily": "5", "days": 3, "platform": "facebook", "goal": "post_promotion",
            "country": "SY", "provinces": ["all"], "gender": "all", "age_min": 18, "age_max": 65, "link": "https://fb.com/x",
            "desc": "d", "media_count": 0, "addons": [], "bundle": False, "whatsapp": "+963933000000", "tg_username": None}
    o = await O.confirm(uid, spec, checkout_key=f"nu-{uid}")
    print(f"رمز خطأ نور المحاكى: {CODE} · المعرّف الاحتياطي: {fb or '—'}")
    print("بعد الدفع:", o["status"], "السعر", o["price_usd"])
    for i in range(1, 5):
        await db.execute("UPDATE orders SET next_retry_at = now() - interval '1 second' WHERE id=$1", o["id"])
        x = await O.submit(o["id"])
        print(f"  محاولة {i}: status={x['status']} · note={x.get('note')}")
        if x["status"] != "paid":
            break
    print("أُرسل لنور telegram_username =", SENT)
    print("رصيد العميل:", await users.get_balance(uid))
    await live.close(); await db.close_pool(); await r.cleanup()
asyncio.run(main())
