"""سيناريوهات السباق من المحاكاة — اختبارات دائمة (v0.9.2). تحتاج Postgres (TEST_DATABASE_URL)، وإلا تُتخطى.

كل اختبار يطلق عمليات متزامنة حقيقية على القاعدة ويتحقق من:
  • لا مال مضاعف (الرصيد = مجموع ledger دائماً)
  • لا deadlock (asyncio.wait_for يفشل الاختبار بدل التعليق)
مكمّل لـ test_double_charge.py (التأكيد/الشراء/تعديل الأدمن).
"""
import asyncio
import os
import random
from decimal import Decimal

import pytest

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL غير محدد")
ADMIN = 1


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def dbready(loop):
    os.environ["DATABASE_URL"] = DSN
    from app.db import pool as db
    loop.run_until_complete(db.init_pool(DSN))
    loop.run_until_complete(db.run_migrations())
    yield db
    loop.run_until_complete(db.close_pool())


def run(loop, coro, timeout=20):
    async def _w():
        return await asyncio.wait_for(coro, timeout)
    return loop.run_until_complete(_w())


def gather(loop, *coros, timeout=20):
    async def _g():
        return await asyncio.wait_for(asyncio.gather(*coros, return_exceptions=True), timeout)
    return loop.run_until_complete(_g())


def new_user(loop, balance="0"):
    from app.db.repo import users
    from app.services import money as M
    uid = random.randint(8_000_000_000, 8_999_999_999)
    run(loop, users.upsert_user(uid, "race", None))
    if Decimal(balance) > 0:
        run(loop, M.credit(uid, Decimal(balance), "topup"))
    return uid


def balance_ok(loop, uid) -> Decimal:
    from app.db.repo import users
    from app.services import money as M
    b = run(loop, users.get_balance(uid))
    assert run(loop, M.ledger_sum(uid)) == b, "الرصيد لا يطابق ledger"
    return b


# ─────────────── الشحن ───────────────

def test_topup_double_approve_credits_once(loop, dbready):
    from app.db.repo import topups
    uid = new_user(loop)
    t = run(loop, topups.create(uid, "usdt_trc20", Decimal("25")))
    res = gather(loop, *[topups.approve(t["id"], ADMIN) for _ in range(4)])
    assert sum(1 for r in res if not isinstance(r, Exception) and r[0]) == 1
    assert balance_ok(loop, uid) == Decimal("25")


def test_topup_approve_vs_reject(loop, dbready):
    from app.db.repo import topups
    uid = new_user(loop)
    t = run(loop, topups.create(uid, "usdt_trc20", Decimal("10")))
    gather(loop, topups.approve(t["id"], ADMIN), topups.reject(t["id"], ADMIN, "x"), topups.approve(t["id"], ADMIN))
    row = run(loop, topups.get(t["id"]))
    b = balance_ok(loop, uid)
    assert (row["status"], b) in (("approved", Decimal("10")), ("rejected", Decimal("0")))


# ─────────────── الطلبات ───────────────

def test_order_double_refund_once(loop, dbready):
    from app.services import orders as O
    uid = new_user(loop, "100")
    order = run(loop, O.confirm(uid, {"kind": "tg_ads", "budget": "10", "addons": []}, checkout_key=f"r-{uid}"))
    after_charge = balance_ok(loop, uid)
    gather(loop, *[O.refund(order["id"], "اختبار", admin_id=ADMIN) for _ in range(4)])
    assert balance_ok(loop, uid) == after_charge + Decimal(str(order["price_usd"]))


def test_different_keys_same_user_serialize_without_overdraw(loop, dbready):
    """5 طلبات مختلفة متزامنة ورصيد يكفي لطلبين فقط ← طلبان بالضبط، بلا رصيد سالب."""
    from app.services import money as M
    from app.services import orders as O
    spec = {"kind": "tg_ads", "budget": "10", "addons": []}
    price = O.compute_prices(spec)[1]
    uid = new_user(loop, str(price * 2))
    res = gather(loop, *[O.confirm(uid, spec, checkout_key=f"k{i}-{uid}") for i in range(5)])
    ok = [r for r in res if isinstance(r, dict)]
    assert len(ok) == 2
    assert all(isinstance(r, M.InsufficientBalance) for r in res if not isinstance(r, dict))
    assert balance_ok(loop, uid) == Decimal("0")


# ─────────────── الباقات المجدولة ───────────────

def _package(loop):
    from app.services import scheduled as SD
    pkg, _ = run(loop, SD.save_package({"code": "racepkg", "title": "باقة سباق", "description": "x", "price_usd": "21",
                                        "total_items": 3, "duration_days": 3, "send_time": "20:00"}))
    return pkg


def test_subscription_double_cancel_refunds_once(loop, dbready):
    from app.services import scheduled as SD
    pkg = _package(loop)
    uid = new_user(loop, "50")
    sub = run(loop, SD.purchase(uid, pkg["code"], checkout_key=f"c-{uid}"))
    gather(loop, *[SD.cancel(sub["id"], ADMIN) for _ in range(4)])
    assert balance_ok(loop, uid) == Decimal("50")   # استرداد كامل مرة واحدة (لم يُرسل شيء)


def test_cancel_and_purchase_concurrently_no_deadlock(loop, dbready):
    """إلغاء اشتراك + شراء آخر لنفس المستخدم في اللحظة نفسها — ترتيب الأقفال موحّد."""
    from app.services import scheduled as SD
    pkg = _package(loop)
    uid = new_user(loop, "100")
    sub = run(loop, SD.purchase(uid, pkg["code"], checkout_key=f"p1-{uid}"))
    for i in range(5):
        gather(loop, SD.cancel(sub["id"], ADMIN), SD.purchase(uid, pkg["code"], checkout_key=f"p{i + 2}-{uid}"))
    balance_ok(loop, uid)


def _activated_sub(loop, db, days_ago: int):
    from datetime import date, timedelta
    from app.services import scheduled as SD
    pkg = _package(loop)
    uid = new_user(loop, "50")
    sub = run(loop, SD.purchase(uid, pkg["code"], checkout_key=f"a-{uid}"))
    for seq in (1, 2, 3):
        run(loop, SD.add_content(sub["id"], seq, "photo", f"FILE{seq}", f"نص {seq}"))
    run(loop, SD.activate(sub["id"], (date.today() - timedelta(days=days_ago)).isoformat(), "00:01"))
    return sub["id"]


def test_due_item_never_double_claims(loop, dbready):
    """عاملان للمجدول معاً لا يحجزان التسليم نفسه."""
    from app.db.repo import scheduled as SR
    sid = _activated_sub(loop, dbready, days_ago=5)
    res = gather(loop, *[SR.due_item() for _ in range(6)])
    mine = [r for r in res if isinstance(r, dict) and r["subscription_id"] == sid]
    assert len({r["id"] for r in mine}) == len(mine)


def test_catchup_one_per_hour_and_retry_backoff(loop, dbready):
    from app.db.repo import scheduled as SR
    db = dbready
    sid = _activated_sub(loop, db, days_ago=5)   # الثلاثة كلها متأخرة

    async def claim_mine():
        # نسحب حتى نجد عنصراً لهذا الاشتراك (قد تبقى عناصر من اختبارات سابقة)
        for _ in range(20):
            it = await SR.due_item()
            if not it:
                return None
            if it["subscription_id"] == sid:
                return it
            await SR.mark_sent(it["id"], it["subscription_id"])
        return None

    first = run(loop, claim_mine())
    assert first and first["seq"] == 1 and int(first["overdue_count"]) >= 2
    run(loop, SR.mark_sent(first["id"], sid))
    assert run(loop, claim_mine()) is None, "لا تسليم ثانٍ للمشترك خلال الساعة"
    run(loop, db.execute("UPDATE scheduled_subscription_items SET sent_at = now() - interval '61 minutes' "
                         "WHERE subscription_id=$1 AND status='sent'", sid))
    second = run(loop, claim_mine())
    assert second and second["seq"] == 2
    # فشل عابر ← لا يُعاد فوراً، بل بعد 10 دقائق
    run(loop, SR.mark_failed(second["id"], sid, "network", retry=True))
    assert run(loop, claim_mine()) is None, "المحاولة التالية يجب أن تنتظر"
    run(loop, db.execute("UPDATE scheduled_subscription_items SET updated_at = now() - interval '11 minutes' WHERE id=$1",
                         second["id"]))
    again = run(loop, claim_mine())
    assert again and again["id"] == second["id"]
