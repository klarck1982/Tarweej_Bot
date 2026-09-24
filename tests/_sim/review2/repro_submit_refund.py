"""إعادة إنتاج: استرداد الأدمن أثناء إرسال الطلب إلى نور ← هل يُسترد المال والحملة تعمل معاً؟"""
import asyncio, os, sys
from decimal import Decimal
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("ADMIN_IDS", "1")
os.environ["DATABASE_URL"] = DSN = "postgresql://postgres@127.0.0.1:5433/promobot"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))


class SlowNour:
    dry_run = True
    calls = 0
    async def create_campaign(self, payload, key):
        SlowNour.calls += 1
        await asyncio.sleep(1.0)                      # طلب HTTP بطيء
        return {"id": f"N{SlowNour.calls}", "charged": payload.get("budget") or "0", "raw": {}}


async def main():
    from app.db import pool as db
    await db.init_pool(DSN); await db.run_migrations()
    from app.db.repo import users
    from app.services import money as M, orders as O, nour
    nour.client = lambda: SlowNour()
    O.build_nour_payload = lambda order: {"title": f"ORD-{order['id']}", "budget": str(order["cost_usd"])}
    import random; uid = random.randint(10**9, 2*10**9)
    await users.upsert_user(uid, "r", None); await M.credit(uid, Decimal("100"), "topup")
    spec = {"kind": "tg_ads", "budget": "10", "addons": []}
    # الإرسال والاسترداد في اللحظة نفسها (المجدول + الأدمن)
    o2 = await O.confirm(uid, spec, checkout_key=f"x2-{uid}")
    await db.execute("UPDATE orders SET kind='meta_campaign', status='paid' WHERE id=$1", o2["id"])
    async def admin_refund():
        await asyncio.sleep(0.3)
        try:
            return await O.refund(o2["id"], "طلب العميل", admin_id=1)
        except O.RefundBusy:
            print("  ⏳ الاسترداد رُفض: الطلب قيد الإرسال (RefundBusy) — سلوك صحيح")
            return None
    s2, r2 = await asyncio.gather(O.submit(o2["id"]), admin_refund())
    final = await db.fetchrow("SELECT status, nour_id, refunded_usd, price_usd FROM orders WHERE id=$1", o2["id"])
    print("refund returned:", type(r2).__name__, r2 and r2.get("status"), "| final:", dict(final))
    if final["refunded_usd"] and final["nour_id"]:
        print("❌ BUG: العميل استرد", final["refunded_usd"], "والحملة أُرسلت لنور", final["nour_id"], "والحالة", final["status"])
    else:
        print("✅ لا تعارض")
    # إرسال مزدوج: المعالج + المجدول معاً لطلب جديد
    o3 = await O.confirm(uid, spec, checkout_key=f"x3-{uid}")
    await db.execute("UPDATE orders SET kind='meta_campaign', status='paid' WHERE id=$1", o3["id"])
    before = SlowNour.calls
    await asyncio.gather(O.submit(o3["id"]), O.submit(o3["id"]))
    print("create_campaign calls for one order:", SlowNour.calls - before)
    await db.close_pool()

asyncio.run(main())
