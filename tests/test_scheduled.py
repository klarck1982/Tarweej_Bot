"""اختبارات باقة التصميم اليومي — منطق الجدولة + الحجز الذرّي + الاسترداد + تدفق التسليم.

    الهدف: منطق نقي بدون شبكة/بوت — يُشغَّل في أي بيئة (بدون قاعدة بيانات حقيقية).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import scheduled as SD  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ══════════════════════════════ حساب المواعيد ══════════════════════════════

def test_first_slot_in_future_with_tz():
    """أول موعد يحسب بتوقيت الزبون ودائماً في المستقبل (UTC)."""
    s = SD.first_slot("20:00", "Asia/Damascus")
    assert s.tzinfo == timezone.utc
    assert s > datetime.now(timezone.utc) + timedelta(minutes=1)
    assert s < datetime.now(timezone.utc) + timedelta(hours=30)


def test_next_daily_slot_gap():
    base = datetime(2026, 1, 1, 17, 0, tzinfo=timezone.utc)
    nxt = SD.next_daily_slot(time(20, 0), "Asia/Damascus", base)
    assert 23 <= (nxt - base).total_seconds() / 3600 <= 25


def test_catchup_slot_is_one_hour_later():
    base = datetime.now(timezone.utc)
    assert abs((SD.catchup_slot(base) - base).total_seconds() - 3600) < 5


def test_time_value_valid_and_invalid():
    assert SD._time_value("09:15") == time(9, 15)
    assert SD._time_value(None) == time(20, 0)
    assert SD._time_value(time(23, 5)) == time(23, 5)
    with pytest.raises(ValueError):
        SD._time_value("25:99")
    with pytest.raises(ValueError):
        SD._time_value("eight")


# ══════════════════════════════ الاسترداد النسبي ══════════════════════════════

def test_refund_quote_proportional():
    """الاسترداد = السعر × المتبقٍ/الإجمالي (بالتساوي عن كل تسليم غير مسلَّم)."""
    sub = {"price_usd": 30, "total_items": 10, "sent_count": 4, "status": "scheduled"}
    q = SD.refund_quote(sub)
    assert float(q) == pytest.approx(18.0)  # 30 × 6/10


def test_refund_quote_edge_cases():
    assert float(SD.refund_quote({"price_usd": 30, "total_items": 10, "sent_count": 10, "status": "completed"})) == 0
    assert float(SD.refund_quote({"price_usd": 30, "total_items": 10, "sent_count": 0, "status": "paused"})) == pytest.approx(30.0)
    # حالة مُغلقة → صفر (idempotent — لا ازدواج استرداد)
    assert float(SD.refund_quote({"price_usd": 30, "total_items": 10, "sent_count": 2, "status": "refunded"})) == 0
    assert float(SD.refund_quote({"price_usd": 30, "total_items": 10, "sent_count": 2, "status": "cancelled"})) == 0


# ══════════════════════════════ تحقق الباقات ══════════════════════════════

def _async(value):
    """يعيد دالة غير متزامنة تعيد القيمة — لتزييف دوال repo."""
    async def _c(*_a, **_k):
        return value
    return _c


def _pkg_data(**over):
    base = {"code": "plan_a", "title": "باقة 30 تصميماً", "description": "وصف",
            "price_usd": "30", "total_items": "30", "send_time": "20:00",
            "timezone": "Asia/Damascus", "order_index": "1"}
    base.update(over)
    return base


def test_validate_package_ok():
    out = SD.validate_package(_pkg_data())
    assert out["code"] == "plan_a"
    assert out["total_items"] == 30
    assert out["send_time"] == "20:00"
    # الموديل القديم مُلغى: العقد بالعدد — المدة شكلية وتساوي العدد
    assert out["duration_days"] == out["total_items"]


def test_validate_package_errors():
    with pytest.raises(ValueError):
        SD.validate_package(_pkg_data(code="A B"))
    with pytest.raises(ValueError):
        SD.validate_package(_pkg_data(title=""))
    with pytest.raises(ValueError):
        SD.validate_package(_pkg_data(price_usd="0"))
    with pytest.raises(ValueError):
        SD.validate_package(_pkg_data(total_items="0"))
    with pytest.raises(ValueError):
        SD.validate_package(_pkg_data(send_time="9pm"))


# ══════════════════════════════ وهم قاعدة البيانات ══════════════════════════════

class _TxCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    """يقلّد اتصال معاملة: fetchrow/fetchval حسب hook، execute تُسجَّل."""

    def __init__(self, rows: dict[str, dict | None], value=None, fetchrow_hook=None):
        self.rows = rows
        self.value = value
        self.executed: list[tuple] = []
        self.fetchrow_hook = fetchrow_hook

    def transaction(self):
        return _TxCM(self)

    async def fetchrow(self, sql, *args):
        if self.fetchrow_hook is not None:
            result = self.fetchrow_hook(sql, *args, executed=self.executed)
            if result is not None:
                return result
        for key, row in self.rows.items():
            if key in sql:
                return row
        return None

    async def fetchval(self, sql, *args):
        return self.value

    async def fetch(self, sql, *args):
        return []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"


def _patch_db(monkeypatch, conn: _FakeConn):
    """يربط وحدة app.db.pool كلها بالاتصال الوهمي (pool/fetchrow/fetchval/execute)."""

    class _Acq:
        async def __aenter__(self_inner):
            return conn

        async def __aexit__(self_inner, *e):
            return False

        def transaction(self_inner):
            return _TxCM(conn)

    class _Pool:
        def acquire(self):
            return _Acq()

    monkeypatch.setattr(SD.db, "pool", lambda: _Pool())
    monkeypatch.setattr(SD.db, "fetchrow", conn.fetchrow)
    monkeypatch.setattr(SD.db, "fetchval", conn.fetchval)
    monkeypatch.setattr(SD.db, "execute", conn.execute)


# ══════════════════════════════ الحجز الذرّي + الحارس ══════════════════════════════

_ITEM = {"id": 5, "seq": 3, "subscription_id": 7, "file_kind": "photo", "file_id": "AAA",
         "copy_text": "نص", "status": "pending", "attempts": 0, "last_error": None}


def test_claim_next_pair_marks_sending(monkeypatch):
    """الزوج يُحجَز sending ذرّياً (UPDATE … SKIP LOCKED) — والحجز يعيد صف post-update."""
    calls = {"n": 0}

    def hook(sql, *args, executed):
        if "UPDATE scheduled_subscription_items" in sql and "SKIP LOCKED" in sql:
            calls["n"] += 1
            if calls["n"] > 1:
                return None
            executed.append((sql, args))
            return {**_ITEM, "status": "sending", "attempts": 1}
        return None

    conn = _FakeConn({}, fetchrow_hook=hook)
    _patch_db(monkeypatch, conn)
    item = run(SD.repo.claim_next_pair(7))
    assert item["status"] == "sending"
    assert item["attempts"] == 1
    assert any("sending" in s for s, _ in conn.executed), "يجب أن يكتب الحجز sending"
    again = run(SD.repo.claim_next_pair(7))
    assert again is None, "لا يُسلَّم نفس الزوج مرتين"


def test_mark_sent_guard(monkeypatch):
    """mark_sent لا يمس حالة ملغية/مستردة — الحارس في SQL (scheduled/paused فقط)."""
    sub_row = {"id": 7, "status": "scheduled", "sent_count": 1,
               "total_items": 5, "next_send_at": None, "last_sent_at": None}
    final_row = {**sub_row, "status": "completed", "sent_count": 5}
    seen_sql: list[str] = []

    def hook(sql, *args, executed):
        seen_sql.append(sql)
        if any("status='sent'" in s for s, _ in executed):
            return final_row
        return sub_row

    conn = _FakeConn({}, fetchrow_hook=hook)
    _patch_db(monkeypatch, conn)
    run(SD.repo.mark_sent(5, 7, missed_chain=False,
                          next_send_at=datetime.now(timezone.utc)))
    all_sql = [s for s, _ in conn.executed] + seen_sql
    assert any("status='sent'" in s for s in all_sql), "يجب أن يُعلَّم الزوج sent"
    assert any("IN ('scheduled','paused')" in s for s in all_sql), \
        "يجب أن يحمي الحارس الحالات المنهية (ملغية/مستردة/مكتملة)"


def test_cancel_refund_math(monkeypatch):
    """الاسترداد = السعر × المتبقي ÷ الإجمالي ويُضاف للرصيد في نفس المعاملة."""
    sub_row = {"id": 9, "status": "scheduled", "sent_count": 2, "total_items": 6,
               "price_usd": 12, "order_id": 3, "user_id": 55, "is_manual": False}
    final_row = {**sub_row, "status": "refunded"}

    def hook(sql, *args, executed):
        if any("status='refunded'" in s for s, _ in executed):
            return final_row
        return sub_row

    conn = _FakeConn({}, value=2, fetchrow_hook=hook)  # count(*) للمنفّذ = 2
    _patch_db(monkeypatch, conn)

    class _M:
        @staticmethod
        async def lock_user(c, uid):
            return None

    monkeypatch.setattr(SD, "money", _M)
    _sub, refund = run(SD.cancel(9))
    assert float(refund) == pytest.approx(8.0)  # 12 × 4/6
    assert any("INSERT INTO ledger" in s for s, _ in conn.executed), "يجب أن يُسجَّل الاسترداد في السجل"
    assert any("balance_usd" in s for s, _ in conn.executed), "يجب أن يُضاف للرصيد"
    assert any("status='refunded'" in s for s, _ in conn.executed), "يجب أن تُعلَّم الحالة refunded"


def test_refund_idempotent_when_final(monkeypatch):
    sub_row = {"id": 9, "status": "refunded", "sent_count": 2, "total_items": 6,
               "price_usd": 12, "order_id": 3, "user_id": 55}
    conn = _FakeConn({"FROM scheduled_subscriptions": sub_row})
    _patch_db(monkeypatch, conn)
    sub, refund = run(SD.cancel(9))
    assert float(refund) == 0
    assert sub["status"] == "refunded"
    assert not conn.executed, "لا كتابة إطلاقاً على اشتراك مُسترد مسبقاً (idempotent)"


# ══════════════════════════════ منطق التسليم — عزل بين المشتركين ══════════════════════════════

class _BotSpy:
    def __init__(self):
        self.calls: list[tuple] = []
        self.deleted: list[tuple] = []
        self._mid = 100

    def _ret(self):
        self._mid += 1
        from types import SimpleNamespace
        return SimpleNamespace(message_id=self._mid)

    async def send_photo(self, chat_id, file_id, caption=None):
        self.calls.append(("photo", chat_id, file_id, caption))
        return self._ret()

    async def send_video(self, chat_id, file_id, caption=None):
        self.calls.append(("video", chat_id, file_id, caption))
        return self._ret()

    async def send_document(self, chat_id, file_id, caption=None):
        self.calls.append(("document", chat_id, file_id, caption))
        return self._ret()

    async def send_message(self, chat_id, text, **kw):
        self.calls.append(("text", chat_id, text, kw.get("caption")))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


def test_deliver_uses_caption_and_isolation(monkeypatch):
    """الزوج يُرسل بالـcaption (لا رسالة منفصلة) لمحادثة صاحبه فقط."""
    bot = _BotSpy()
    item = {"id": 5, "seq": 2, "subscription_id": 7, "file_kind": "photo", "file_id": "PH",
            "copy_text": "🎨 التصميم الثاني", "status": "sending", "attempts": 0}
    sub = {"id": 7, "status": "scheduled", "sent_count": 1, "total_items": 5,
           "next_send_at": None, "target_chat_id": 999, "target_kind": "user",
           "send_time": "20:00", "timezone": "Asia/Damascus", "missed_slot": False}

    async def fake_claim(sub_id):
        return dict(item)

    async def fake_get_sub(sub_id):
        return dict(sub)

    async def fake_mark_sent(item_id, sub_id, **kw):
        return {**sub, "sent_count": 2}

    conn = _FakeConn({}, value=0)  # more_ready = 0
    _patch_db(monkeypatch, conn)
    monkeypatch.setattr(SD.repo, "claim_next_pair", fake_claim)
    monkeypatch.setattr(SD.repo, "get_subscription", fake_get_sub)
    monkeypatch.setattr(SD.repo, "mark_sent", fake_mark_sent)
    result, _info = run(SD.deliver_next(bot, 7))
    assert result == "sent"
    photos = [c for c in bot.calls if c[0] == "photo"]
    assert photos and photos[0][1] == 999  # chat_id هو هدف الاشتراك (لا تسرب لأدمن)
    assert photos[0][3] == "🎨 التصميم الثاني"  # caption لا رسالة منفصلة
    assert not [c for c in bot.calls if c[0] == "text" and "التصميم" in str(c[2])]
    assert any("sent_message_id" in s for s, _ in conn.executed), \
        "يجب أن يُخزَّن معرّف الرسالة للحذف لاحقاً (معاينة ثم تنظيف)"


def test_delete_sent_message_cleans_target(monkeypatch):
    """زر التنظيف: يحذف رسالة المعاينة من الوجه ويمسح المعرّف — ولا يمس حالة الزوج sent."""
    bot = _BotSpy()
    sub = {"id": 7, "status": "scheduled", "target_chat_id": 999, "user_id": 55}
    item = {"id": 5, "seq": 2, "subscription_id": 7, "status": "sent",
            "sent_message_id": 101, "file_id": "PH"}

    async def fake_get_sub(sub_id):
        return dict(sub)

    async def fake_get_item(sub_id, seq):
        return dict(item)

    cleared = {}

    async def fake_set_sent(mid, message_id):
        cleared["v"] = message_id

    monkeypatch.setattr(SD.repo, "get_subscription", fake_get_sub)
    monkeypatch.setattr(SD.repo, "get_item", fake_get_item)
    monkeypatch.setattr(SD.repo, "set_sent_message", fake_set_sent)
    ok, err = run(SD.delete_sent_message(bot, 7, 2))
    assert ok and err is None
    assert bot.deleted == [(999, 101)], "يجب أن يُحذف المنشور من وجه الاشتراك فقط"
    assert cleared["v"] is None, "يجب أن يُمسح معرّف الرسالة"
    # مرة ثانية: لا رسالة محفوظة → رفض لطيف
    async def fake_get_item_gone(sub_id, seq):
        return {**item, "sent_message_id": None}
    monkeypatch.setattr(SD.repo, "get_item", fake_get_item_gone)
    ok2, err2 = run(SD.delete_sent_message(bot, 7, 2))
    assert not ok2 and err2


def test_run_due_is_thin(monkeypatch):
    """الاستدعاء من الـscheduler يمر عبر run_due (لا منطق جدولة خارجي)."""
    rows = [{"id": 7}, {"id": 8}]
    sent_calls = []

    async def fake_due(*a, **kw):
        return rows.pop(0) if rows else None

    async def fake_deliver(bot, sid, **kw):
        sent_calls.append(sid)
        return ("sent", {}) if sid == 7 else ("empty", {})

    async def fake_update(*a, **kw):
        return None

    monkeypatch.setattr(SD.repo, "due_subscription", fake_due)
    monkeypatch.setattr(SD, "deliver_next", fake_deliver)
    monkeypatch.setattr(SD.repo, "update_fields", fake_update)
    n = run(SD.run_due(None, limit=3))
    assert n == 1
    assert sent_calls == [7, 8], "يمر على المستحق بالترتيب ويتوقف عند الفراغ"


# ── تفعيل اشتراك «بانتظار المحتوى» ──
def test_activate_requires_pairs_then_starts_schedule(monkeypatch):
    """لا تفعيل بلا زوج جاهز — وبعده تصبح scheduled وموعدها محسوب."""
    sub = {"id": 7, "status": "awaiting_assets", "send_time": "20:00",
           "timezone_name": "Asia/Damascus", "ready_items": 0, "next_send_at": None}
    monkeypatch.setattr(SD.repo, "get_subscription", _async(dict(sub)))

    # (1) بلا أزواج → رفض
    try:
        run(SD.activate(7))
        raise AssertionError("يجب أن يُرفض التفعيل بلا أزواج")
    except ValueError as e:
        assert "زوجاً" in str(e)

    # (2) بعد رفع زوج → يُفعّل ويُحسب الموعد
    sub2 = dict(sub, ready_items=2)
    monkeypatch.setattr(SD.repo, "get_subscription", _async(dict(sub2)))
    monkeypatch.setattr(SD.repo, "set_status", _async({"id": 7, "status": "scheduled",
                                                       "send_time": "20:00",
                                                       "timezone_name": "Asia/Damascus"}))
    saved = {}

    async def fake_update_fields(sid, **fields):
        saved.update(fields)
        return {"id": sid}

    monkeypatch.setattr(SD.repo, "update_fields", fake_update_fields)
    out = run(SD.activate(7))
    assert out["status"] == "awaiting_assets"      # التزييف لا يغيّر الحالة
    assert "next_send_at" in saved and saved["next_send_at"] is not None
    assert saved.get("next_send_at") > datetime.now(timezone.utc), "الموعد يجب أن يكون مستقبلياً"

    # (3) حالة نهائية → رفض
    sub3 = dict(sub, ready_items=3, status="completed")
    monkeypatch.setattr(SD.repo, "get_subscription", _async(dict(sub3)))
    try:
        run(SD.activate(7))
        raise AssertionError("يجب رفض تفعيل اشتراك مكتمل")
    except ValueError:
        pass


def test_activate_idempotent_when_already_scheduled(monkeypatch):
    sub = {"id": 9, "status": "scheduled", "send_time": "20:00",
           "timezone_name": "Asia/Damascus", "ready_items": 4}
    monkeypatch.setattr(SD.repo, "get_subscription", _async(dict(sub)))
    out = run(SD.activate(9))
    assert out["status"] == "scheduled"
