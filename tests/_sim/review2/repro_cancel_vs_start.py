"""إلغاء العميل ⟷ بدء/نشر الأدمن في اللحظة نفسها (تصميم + منشور شريك)."""
import asyncio, os, sys, random
from decimal import Decimal
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("ADMIN_IDS", "1")
os.environ["DATABASE_URL"] = DSN = "postgresql://postgres@127.0.0.1:5433/promobot"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

async def main():
    from app.db import pool as db
    await db.init_pool(DSN); await db.run_migrations()
    from app.db.repo import users
    from app.services import money as M, orders as O, design as D, partner_posts as PP
    bad = {"design": 0, "tg_post": 0}; N = 30
    for kind in ("design", "tg_post"):
        for i in range(N):
            uid = random.randint(10**9, 2*10**9)
            await users.upsert_user(uid, "r", None); await M.credit(uid, Decimal("100"), "topup")
            o = await O.confirm(uid, {"kind": "tg_ads", "budget": "10", "addons": []}, checkout_key=f"c{uid}")
            spec = {"hours": 24, "price": str(o["price_usd"])}
            await db.execute("UPDATE orders SET kind=$2, status='submitted', spec=$3::jsonb WHERE id=$1",
                             o["id"], kind, __import__("json").dumps(spec))
            if kind == "design":
                admin = D.start(o["id"], 1); user = D.cancel_by_user(o["id"], uid)
            else:
                admin = PP.publish(o["id"], 1, "https://t.me/x/1"); user = PP.cancel_by_user(o["id"], uid)
            await asyncio.gather(admin, user, return_exceptions=True)
            r = await db.fetchrow("SELECT status, refunded_usd FROM orders WHERE id=$1", o["id"])
            if r["refunded_usd"] and r["status"] not in ("cancelled", "refunded", "rejected"):
                bad[kind] += 1
                if bad[kind] == 1:
                    print(f"  مثال {kind}: status={r['status']} refunded={r['refunded_usd']}")
    print("حالات (مُسترد + ما زال مفتوحاً للعمل):", bad, "من", N, "لكل نوع")
    await db.close_pool()
asyncio.run(main())
