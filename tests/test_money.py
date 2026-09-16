"""اختبارات القواعد المالية — تحتاج Postgres محلياً (تُتخطى تلقائياً إن لم يتوفر TEST_DATABASE_URL)."""
import asyncio
import os
from decimal import Decimal

import pytest

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL غير محدد")


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
    loop.run_until_complete(db.execute("DELETE FROM ledger WHERE user_id = 424242; DELETE FROM topups WHERE user_id = 424242; DELETE FROM users WHERE tg_id = 424242"))
    yield db
    loop.run_until_complete(db.close_pool())


def test_credit_debit_and_ledger_match(loop, dbready):
    from app.db.repo import users
    from app.services import money as M
    loop.run_until_complete(users.upsert_user(424242, "test", None))
    b1 = loop.run_until_complete(M.credit(424242, Decimal("10"), "topup"))
    assert b1 == Decimal("10.00")
    b2 = loop.run_until_complete(M.debit(424242, Decimal("4.50"), "order_charge"))
    assert b2 == Decimal("5.50")
    with pytest.raises(M.InsufficientBalance):
        loop.run_until_complete(M.debit(424242, Decimal("6"), "order_charge"))
    assert loop.run_until_complete(M.ledger_sum(424242)) == loop.run_until_complete(users.get_balance(424242)) == Decimal("5.50")


def test_concurrent_debits_never_overdraw(loop, dbready):
    """20 خصماً متزامناً بقيمة 1$ من رصيد 5.50$ → ينجح 5 فقط."""
    from app.db.repo import users
    from app.services import money as M

    async def run():
        results = await asyncio.gather(*[M.debit(424242, Decimal("1"), "order_charge") for _ in range(20)], return_exceptions=True)
        ok = [r for r in results if not isinstance(r, Exception)]
        fail = [r for r in results if isinstance(r, M.InsufficientBalance)]
        return ok, fail

    ok, fail = loop.run_until_complete(run())
    assert len(ok) == 5 and len(fail) == 15
    assert loop.run_until_complete(users.get_balance(424242)) == Decimal("0.50")
    assert loop.run_until_complete(M.ledger_sum(424242)) == Decimal("0.50")


def test_topup_approve_once(loop, dbready):
    from app.db.repo import topups
    row = loop.run_until_complete(topups.create(424242, "usdt_trc20", Decimal("7")))
    ok1, bal1, _ = loop.run_until_complete(topups.approve(row["id"], 1))
    ok2, bal2, _ = loop.run_until_complete(topups.approve(row["id"], 1))
    assert ok1 and bal1 == Decimal("7.50")
    assert not ok2 and bal2 is None
