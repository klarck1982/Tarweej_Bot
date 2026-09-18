"""اختبارات v0.8.1: التذاكر، شرائح البث، البحث وتعديل الرصيد."""
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
    os.environ.setdefault("ADMIN_IDS", "999")
    os.environ["DATABASE_URL"] = DSN
    from app.db import pool as db
    loop.run_until_complete(db.init_pool(DSN))
    loop.run_until_complete(db.run_migrations())
    loop.run_until_complete(db.execute(
        "TRUNCATE fsm_state, events, ledger, topups, ticket_messages, tickets, order_media, orders RESTART IDENTITY CASCADE"
    ))
    loop.run_until_complete(db.execute("DELETE FROM users WHERE tg_id IN (8101, 8102, 999)"))
    yield db
    loop.run_until_complete(db.close_pool())


def _user(loop, uid=8101, name="اختبار", username="v081"):
    from app.db.repo import users
    loop.run_until_complete(users.upsert_user(uid, name, username))
    loop.run_until_complete(users.accept_terms(uid))


def test_ticket_lifecycle(loop, dbready):
    from app.db.repo import tickets
    _user(loop)
    t = loop.run_until_complete(tickets.create(8101, kind="question"))
    loop.run_until_complete(tickets.add_message(t["id"], 8101, False, "أحتاج مساعدة"))
    assert loop.run_until_complete(tickets.get(t["id"]))["status"] == "open"
    loop.run_until_complete(tickets.add_message(t["id"], 999, True, "تفضل، كيف أساعدك؟"))
    assert loop.run_until_complete(tickets.get(t["id"]))["status"] == "answered"
    assert len(loop.run_until_complete(tickets.messages(t["id"]))) == 2
    assert loop.run_until_complete(tickets.close(t["id"], 8101))["status"] == "closed"


def test_manual_balance_is_atomic_and_logged(loop, dbready):
    from app.db.repo import users
    from app.services import money
    _user(loop, 8102, "رصيد", "balance_test")
    assert loop.run_until_complete(money.credit(8102, Decimal("7"), "adjustment", ref_type="admin", note="تعويض", admin_id=999)) == Decimal("7.00")
    assert loop.run_until_complete(money.debit(8102, Decimal("2.25"), "adjustment", ref_type="admin", note="تصحيح", admin_id=999)) == Decimal("4.75")
    with pytest.raises(money.InsufficientBalance):
        loop.run_until_complete(money.debit(8102, Decimal("5"), "adjustment", ref_type="admin", note="زائد", admin_id=999))
    assert loop.run_until_complete(users.get_balance(8102)) == loop.run_until_complete(money.ledger_sum(8102)) == Decimal("4.75")


def test_admin_search_and_broadcast_segments(loop, dbready):
    from app.db.repo import users
    _user(loop, 8101, "سامر", "samer_v081")
    assert loop.run_until_complete(users.admin_find("@samer_v081"))["tg_id"] == 8101
    loop.run_until_complete(dbready.execute("UPDATE users SET username = '@samer_v081' WHERE tg_id = 8101"))
    assert loop.run_until_complete(users.admin_find("samer_v081"))["tg_id"] == 8101
    loop.run_until_complete(dbready.execute("UPDATE users SET username = 'samer_v081' WHERE tg_id = 8101"))
    assert loop.run_until_complete(users.admin_find("8101"))["tg_id"] == 8101
    oid = loop.run_until_complete(dbready.fetchval(
        "INSERT INTO orders (user_id, kind, status, spec, price_usd, cost_usd) VALUES ($1, 'copy', 'completed', '{}'::jsonb, 5, 0) RETURNING id", 8101
    ))
    assert loop.run_until_complete(users.admin_find(f"#ORD-{oid}"))["tg_id"] == 8101
    counts = loop.run_until_complete(users.broadcast_counts())
    assert counts["all"] >= 1 and counts["new"] >= 1
    recipients = loop.run_until_complete(users.broadcast_recipients("new"))
    assert any(x["tg_id"] == 8101 for x in recipients)


def test_ticket_auto_close_after_72_hours(loop, dbready):
    from app.db.repo import tickets
    _user(loop, 8101, "سامر", "samer_v081")
    t = loop.run_until_complete(tickets.create(8101, kind="question"))
    loop.run_until_complete(tickets.add_message(t["id"], 999, True, "تم الرد"))
    loop.run_until_complete(dbready.execute("UPDATE tickets SET last_msg_at = now() - interval '73 hours' WHERE id = $1", t["id"]))
    closed = loop.run_until_complete(tickets.auto_close())
    assert any(x["id"] == t["id"] for x in closed)
    assert loop.run_until_complete(tickets.get(t["id"]))["status"] == "closed"
