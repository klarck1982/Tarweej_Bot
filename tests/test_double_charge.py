"""اختبارات منع الخصم المزدوج (v0.9.2) — تحتاج Postgres (تُتخطى تلقائياً إن لم يتوفر TEST_DATABASE_URL).

تغطي ثغرات تقرير المحاكاة:
  1) تأكيد الطلب مرتين متزامنتين/متتابعتين بنفس رمز الشراء ← طلب واحد وخصم واحد وبلا deadlock
  2) شراء باقة مجدولة مرتين بنفس الرمز ← اشتراك واحد
  3) تعديل رصيد إداري مكرر بنفس المفتاح ← يُطبَّق مرة واحدة
"""
import asyncio
import os
from decimal import Decimal

import pytest

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL غير محدد")
import random
UID = random.randint(7_000_000_000, 7_999_999_999)   # مستخدم جديد في كل تشغيل — لا حاجة لتنظيف


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def dbready(loop):
    os.environ.setdefault("BOT_TOKEN", "1:a")
    os.environ.setdefault("ADMIN_IDS", "1")
    os.environ["DATABASE_URL"] = DSN
    from app.db import pool as db
    loop.run_until_complete(db.init_pool(DSN))
    loop.run_until_complete(db.run_migrations())
    from app.db.repo import users
    from app.services import money as M
    loop.run_until_complete(users.upsert_user(UID, "race", None))
    loop.run_until_complete(M.credit(UID, Decimal("500"), "topup"))
    yield db
    loop.run_until_complete(db.close_pool())


def _counts(loop, db, table, key):
    return loop.run_until_complete(db.fetchval(f"SELECT count(*) FROM {table} WHERE user_id=$1 AND checkout_key=$2", UID, key))


def _balance_matches_ledger(loop):
    from app.db.repo import users
    from app.services import money as M
    b = loop.run_until_complete(users.get_balance(UID))
    assert loop.run_until_complete(M.ledger_sum(UID)) == b
    return b


@pytest.mark.parametrize("spec", [
    {"kind": "meta_campaign", "platform": "instagram", "daily": "5", "days": "2", "addons": []},
    {"kind": "tg_ads", "budget": "10", "addons": []},
])
def test_concurrent_confirm_same_key_charges_once(loop, dbready, spec):
    from app.services import orders as O
    key = f"chk-{UID}-{spec['kind']}"
    b0 = _balance_matches_ledger(loop)

    async def burst():
        return await asyncio.wait_for(asyncio.gather(*[O.confirm(UID, spec, checkout_key=key) for _ in range(5)]), 15)
    res = loop.run_until_complete(burst())       # wait_for ← أي deadlock يفشل الاختبار بدل التعليق
    ids = {r["id"] for r in res}
    assert len(ids) == 1, "يجب أن تعيد كل الضغطات الطلب نفسه"
    assert sum(1 for r in res if not r.get("duplicate")) == 1
    assert _counts(loop, dbready, "orders", key) == 1
    price = Decimal(str(res[0]["price_usd"]))
    assert _balance_matches_ledger(loop) == b0 - price
    # ضغطة لاحقة (زر قديم) لا تخصم أيضاً
    again = loop.run_until_complete(O.confirm(UID, spec, checkout_key=key))
    assert again["duplicate"] and again["id"] in ids
    assert _balance_matches_ledger(loop) == b0 - price


def test_scheduled_purchase_same_key_once(loop, dbready):
    from app.services import scheduled as SD
    pkg, _ = loop.run_until_complete(SD.save_package({
        "code": "racetest", "title": "باقة اختبار", "description": "اختبار", "price_usd": "30",
        "total_items": 7, "duration_days": 7, "send_time": "20:00"}))
    pk = [pkg]
    code = pkg["code"]
    key = f"sub-{UID}-t1"
    b0 = _balance_matches_ledger(loop)
    async def burst():
        return await asyncio.wait_for(asyncio.gather(*[SD.purchase(UID, code, checkout_key=key) for _ in range(4)]), 15)
    res = loop.run_until_complete(burst())
    assert len({r["id"] for r in res}) == 1
    assert _counts(loop, dbready, "scheduled_subscriptions", key) == 1
    assert _balance_matches_ledger(loop) == b0 - Decimal(str(pk[0]["price_usd"]))


def test_admin_adjust_idempotent(loop, dbready):
    from app.services import money as M
    b0 = _balance_matches_ledger(loop)

    async def one():
        try:
            await M.credit(UID, Decimal("7"), "adjustment", ref_type="admin", idem_key=f"adj-test-{UID}")
            return "ok"
        except M.DuplicateOperation:
            return "dup"
    async def burst():
        return await asyncio.gather(*[one() for _ in range(4)])
    res = loop.run_until_complete(burst())
    assert res.count("ok") == 1 and res.count("dup") == 3
    assert _balance_matches_ledger(loop) == b0 + Decimal("7")
    with pytest.raises(M.DuplicateOperation):
        loop.run_until_complete(M.debit(UID, Decimal("7"), "adjustment", idem_key=f"adj-test-{UID}"))
    assert _balance_matches_ledger(loop) == b0 + Decimal("7")
