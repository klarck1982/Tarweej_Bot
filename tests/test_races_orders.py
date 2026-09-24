"""تسابقات انتقالات حالة الطلبات (v0.9.3 — المرحلة 6). تحتاج Postgres (TEST_DATABASE_URL)، وإلا تُتخطى.

الثابت المالي في كل اختبار: لا يجتمع «استرداد» مع «خدمة ما زالت تُنفَّذ»، ولا يُخصم رسم دون أثره،
والرصيد يطابق ledger دائماً.
"""
import asyncio
import json
import os
import random
from decimal import Decimal

import pytest

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL غير محدد")
ADMIN = 1
CLOSED = ("cancelled", "refunded", "rejected", "failed_submit")


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


def run(loop, coro, timeout=30):
    async def _w():
        return await asyncio.wait_for(coro, timeout)
    return loop.run_until_complete(_w())


def gather(loop, *coros, timeout=30):
    async def _g():
        return await asyncio.wait_for(asyncio.gather(*coros, return_exceptions=True), timeout)
    return loop.run_until_complete(_g())


def new_order(loop, db, kind: str, status: str, **extra):
    """مستخدم برصيد + طلب مدفوع فعلاً (خصم حقيقي في ledger) ثم نضبط نوعه وحالته."""
    from app.db.repo import users
    from app.services import money as M, orders as O
    uid = random.randint(7_000_000_000, 7_999_999_999)
    run(loop, users.upsert_user(uid, "race6", None))
    run(loop, M.credit(uid, Decimal("100"), "topup"))
    o = run(loop, O.confirm(uid, {"kind": "tg_ads", "budget": "10", "addons": []}, checkout_key=f"r6-{uid}"))
    spec = {"hours": 24, "price": str(o["price_usd"])}
    sets = ", ".join(f"{k} = ${i + 4}" for i, k in enumerate(extra))
    run(loop, db.execute(
        f"UPDATE orders SET kind = $2, status = $3, spec = '{json.dumps(spec)}'::jsonb{', ' + sets if sets else ''} WHERE id = $1",
        o["id"], kind, status, *extra.values()))
    return uid, o["id"]


def state(loop, db, oid):
    return dict(run(loop, db.fetchrow(
        "SELECT status, nour_id, refunded_usd, price_usd, revision_count, submitting_until FROM orders WHERE id = $1", oid)))


def ledger_ok(loop, uid):
    from app.db.repo import users
    from app.services import money as M
    assert run(loop, M.ledger_sum(uid)) == run(loop, users.get_balance(uid)), "الرصيد لا يطابق ledger"


class FakeNour:
    """نور وهمي بطيء قابل للبرمجة."""
    dry_run = True

    def __init__(self, delay=0.5, error=None, found=None, find_error=None):
        self.delay, self.error, self.found, self.find_error = delay, error, found, find_error
        self.calls = 0

    async def create_campaign(self, payload, key):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return {"id": f"N{self.calls}", "charged": None, "raw": {}}

    async def find_by_title(self, title):
        if self.find_error:
            raise self.find_error
        return self.found


@pytest.fixture
def fake_nour(monkeypatch):
    from app.services import nour, orders as O
    holder = {}

    def install(**kw):
        f = FakeNour(**kw)
        holder["f"] = f
        monkeypatch.setattr(nour, "client", lambda: f)
        monkeypatch.setattr(nour, "is_dry_run", lambda: True)
        monkeypatch.setattr(O, "build_nour_payload", lambda order: {"title": f"ORD-{order['id']}"})
        return f
    return install


# ─────────────── Meta / نور ───────────────

def test_refund_during_nour_submit_is_blocked(loop, dbready, fake_nour):
    from app.services import orders as O
    fake_nour(delay=0.6)
    uid, oid = new_order(loop, dbready, "meta_campaign", "paid")

    async def admin_refund():
        await asyncio.sleep(0.2)
        return await O.refund(oid, "طلب العميل", admin_id=ADMIN, expect=("paid", "submitted"))
    sub, ref = gather(loop, O.submit(oid), admin_refund())
    assert isinstance(ref, O.RefundBusy)
    s = state(loop, dbready, oid)
    assert s["status"] == "submitted" and s["nour_id"] and s["refunded_usd"] == 0 and s["submitting_until"] is None
    ledger_ok(loop, uid)


def test_refund_after_submit_finishes_works(loop, dbready, fake_nour):
    """الحجز يُفكّ بعد حفظ النتيجة — الاسترداد العادي لاحقاً يعمل."""
    from app.services import orders as O
    fake_nour(delay=0)
    uid, oid = new_order(loop, dbready, "meta_campaign", "paid")
    run(loop, O.submit(oid))
    o = run(loop, O.refund(oid, "x", admin_id=ADMIN, expect=("submitted",)))
    assert o and o["status"] == "refunded" and o["refunded_usd"] == o["price_usd"]
    ledger_ok(loop, uid)


def test_concurrent_submits_call_nour_once(loop, dbready, fake_nour):
    from app.services import orders as O
    f = fake_nour(delay=0.4)
    uid, oid = new_order(loop, dbready, "meta_campaign", "paid")
    gather(loop, *[O.submit(oid) for _ in range(4)])
    assert f.calls == 1
    assert state(loop, dbready, oid)["status"] == "submitted"


def test_network_exhausted_but_campaign_exists_no_refund(loop, dbready, fake_nour):
    from app.services import nour, orders as O
    fake_nour(delay=0, error=nour.NourError("network", "timeout"), found={"id": 777, "status": "active"})
    uid, oid = new_order(loop, dbready, "meta_campaign", "paid", submit_attempts=O.MAX_SUBMIT_ATTEMPTS - 1)
    run(loop, O.submit(oid))
    s = state(loop, dbready, oid)
    assert s["refunded_usd"] == 0 and s["nour_id"] == "777" and s["status"] == "active"


def test_network_exhausted_and_nour_unreachable_waits(loop, dbready, fake_nour):
    from app.services import nour, orders as O
    fake_nour(delay=0, error=nour.NourError("network", "timeout"), find_error=nour.NourError("network", "down"))
    uid, oid = new_order(loop, dbready, "meta_campaign", "paid", submit_attempts=O.MAX_SUBMIT_ATTEMPTS - 1)
    o = run(loop, O.submit(oid))
    s = state(loop, dbready, oid)
    assert s["status"] == "paid" and s["refunded_usd"] == 0 and s["submitting_until"] is None
    assert "لم يُسترد" in (o.get("note") or "")


def test_network_exhausted_and_not_found_refunds(loop, dbready, fake_nour):
    from app.services import nour, orders as O
    fake_nour(delay=0, error=nour.NourError("network", "timeout"), found=None)
    uid, oid = new_order(loop, dbready, "meta_campaign", "paid", submit_attempts=O.MAX_SUBMIT_ATTEMPTS - 1)
    run(loop, O.submit(oid))
    s = state(loop, dbready, oid)
    assert s["status"] == "failed_submit" and s["refunded_usd"] == s["price_usd"] and s["submitting_until"] is None
    ledger_ok(loop, uid)


def test_non_retryable_error_refunds_immediately(loop, dbready, fake_nour):
    from app.services import nour, orders as O
    fake_nour(delay=0, error=nour.NourError("validation_error", "bad"))
    uid, oid = new_order(loop, dbready, "meta_campaign", "paid")
    run(loop, O.submit(oid))
    assert state(loop, dbready, oid)["status"] == "failed_submit"
    ledger_ok(loop, uid)


def test_nour_sync_does_not_overwrite_admin_refund(loop, dbready):
    from app.services import orders as O
    for _ in range(10):
        uid, oid = new_order(loop, dbready, "meta_campaign", "submitted", nour_id="N-x")
        gather(loop, O.refund(oid, "x", admin_id=ADMIN, expect=("submitted",)), O.apply_nour_status(oid, "active", {}))
        s = state(loop, dbready, oid)
        assert not (s["refunded_usd"] > 0 and s["status"] not in CLOSED), s
        ledger_ok(loop, uid)


# ─────────────── منشورات الشركاء / التصميم ───────────────

@pytest.mark.parametrize("kind", ["tg_post", "design"])
def test_user_cancel_vs_admin_action(loop, dbready, kind):
    from app.services import design as D, partner_posts as PP
    for _ in range(15):
        uid, oid = new_order(loop, dbready, kind, "submitted")
        if kind == "tg_post":
            admin, user = PP.publish(oid, ADMIN, "https://t.me/x/1"), PP.cancel_by_user(oid, uid)
        else:
            admin, user = D.start(oid, ADMIN), D.cancel_by_user(oid, uid)
        a, u = gather(loop, admin, user)
        s = state(loop, dbready, oid)
        assert not (s["refunded_usd"] > 0 and s["status"] not in CLOSED), s
        assert bool(a) != bool(u), "يجب أن ينجح طرف واحد فقط"
        ledger_ok(loop, uid)


def test_admin_reject_vs_publish(loop, dbready):
    from app.services import partner_posts as PP
    for _ in range(10):
        uid, oid = new_order(loop, dbready, "tg_post", "in_progress")
        gather(loop, PP.publish(oid, ADMIN, "https://t.me/x/2"), PP.reject(oid, ADMIN, "لا"))
        s = state(loop, dbready, oid)
        assert (s["status"], s["refunded_usd"] > 0) in (("active", False), ("rejected", True)), s
        ledger_ok(loop, uid)


def test_auto_approve_vs_paid_revision(loop, dbready):
    from app.services import design as D
    for _ in range(15):
        uid, oid = new_order(loop, dbready, "design", "delivered", revision_count=5)
        gather(loop, D.approve(oid, None, auto=True), D.request_revision(oid, uid, "غيّر اللون"))
        s = state(loop, dbready, oid)
        if s["revision_count"] == 6:            # دُفع رسم التعديل ← يجب أن يبقى الطلب للتعديل
            assert s["status"] == "needs_revision", s
        else:
            assert s["status"] == "completed", s
        ledger_ok(loop, uid)
