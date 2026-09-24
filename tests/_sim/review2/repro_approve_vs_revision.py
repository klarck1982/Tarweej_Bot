"""اعتماد تلقائي (المجدول) ⟷ طلب تعديل مدفوع من العميل في اللحظة نفسها."""
import asyncio, os, sys, random, json
from decimal import Decimal
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("ADMIN_IDS", "1")
os.environ["DATABASE_URL"] = DSN = "postgresql://postgres@127.0.0.1:5433/promobot"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

async def main():
    from app.db import pool as db
    await db.init_pool(DSN); await db.run_migrations()
    from app.db.repo import users
    from app.services import money as M, orders as O, design as D
    bad = 0; N = 40
    for i in range(N):
        uid = random.randint(10**9, 2*10**9)
        await users.upsert_user(uid, "r", None); await M.credit(uid, Decimal("100"), "topup")
        o = await O.confirm(uid, {"kind": "tg_ads", "budget": "10", "addons": []}, checkout_key=f"a{uid}")
        await db.execute("UPDATE orders SET kind='design', status='delivered', revision_count=5, spec=$2::jsonb WHERE id=$1",
                         o["id"], json.dumps({"price": "10"}))
        await asyncio.gather(D.approve(o["id"], None, auto=True), D.request_revision(o["id"], uid, "غيّر اللون"),
                             return_exceptions=True)
        r = await db.fetchrow("SELECT status, revision_count FROM orders WHERE id=$1", o["id"])
        if r["revision_count"] == 6 and r["status"] == "completed":
            bad += 1
            if bad == 1:
                print("  مثال: دُفع رسم تعديل لكن الطلب أُغلق completed")
    print(f"رسم تعديل مخصوم + الطلب مكتمل: {bad}/{N}")
    await db.close_pool()
asyncio.run(main())
