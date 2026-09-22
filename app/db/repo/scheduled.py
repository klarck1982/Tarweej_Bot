"""بيانات باقات التصميم المجدول وتسليماتها اليومية."""

from __future__ import annotations

import json
from datetime import datetime, time
from decimal import Decimal

from app.db import pool as db


def _time_value(value: str | time) -> time:
    """asyncpg يرسل TIME كـ datetime.time وليس كسلسلة نصية."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    raw = str(value or "20:00").strip()
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
        return time(hour, minute)
    except (ValueError, TypeError):
        raise ValueError("وقت الإرسال يجب أن يكون بصيغة HH:MM") from None


def _j(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _row(row) -> dict | None:
    return dict(row) if row else None


async def get_subscription(subscription_id: int) -> dict | None:
    row = await db.fetchrow(
        """
        SELECT s.*, u.name AS user_name, u.username AS user_username,
               u.balance_usd AS user_balance
        FROM scheduled_subscriptions s
        JOIN users u ON u.tg_id = s.user_id
        WHERE s.id = $1
        """,
        subscription_id,
    )
    return _row(row)


async def list_subscriptions(query: str = "", limit: int = 40, offset: int = 0) -> list[dict]:
    q = (query or "").strip().lstrip("@").lower()
    rows = await db.fetch(
        """
        SELECT s.*, u.name AS user_name, u.username AS user_username,
               u.balance_usd AS user_balance,
               (SELECT count(*) FROM scheduled_subscription_items i WHERE i.subscription_id = s.id) AS items_count,
               (SELECT count(*) FROM scheduled_subscription_items i WHERE i.subscription_id = s.id AND i.status = 'sent') AS sent_items
        FROM scheduled_subscriptions s
        JOIN users u ON u.tg_id = s.user_id
        WHERE ($1 = '' OR lower(coalesce(u.name,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(u.username,'')) LIKE '%' || $1 || '%'
               OR s.user_id::text = $1 OR s.id::text = $1
               OR lower(s.package_title) LIKE '%' || $1 || '%')
        ORDER BY s.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        q, max(1, min(int(limit), 100)), max(0, int(offset)),
    )
    return [dict(r) for r in rows]


async def count_subscriptions(query: str = "") -> int:
    q = (query or "").strip().lstrip("@").lower()
    return int(await db.fetchval(
        """
        SELECT count(*)
        FROM scheduled_subscriptions s JOIN users u ON u.tg_id = s.user_id
        WHERE ($1 = '' OR lower(coalesce(u.name,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(u.username,'')) LIKE '%' || $1 || '%'
               OR s.user_id::text = $1 OR s.id::text = $1
               OR lower(s.package_title) LIKE '%' || $1 || '%')
        """,
        q,
    ) or 0)


async def items(subscription_id: int) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM scheduled_subscription_items WHERE subscription_id = $1 ORDER BY seq",
        subscription_id,
    )
    return [dict(r) for r in rows]


async def get_item(subscription_id: int, seq: int) -> dict | None:
    return _row(await db.fetchrow(
        "SELECT * FROM scheduled_subscription_items WHERE subscription_id = $1 AND seq = $2",
        subscription_id, seq,
    ))


async def upsert_item(subscription_id: int, seq: int, file_kind: str, file_id: str, copy_text: str = "") -> dict:
    row = await db.fetchrow(
        """
        INSERT INTO scheduled_subscription_items
            (subscription_id, seq, file_kind, file_id, copy_text, status, updated_at)
        VALUES ($1, $2, $3, $4, $5, 'pending', now())
        ON CONFLICT (subscription_id, seq) DO UPDATE SET
            file_kind = EXCLUDED.file_kind,
            file_id = EXCLUDED.file_id,
            copy_text = EXCLUDED.copy_text,
            status = CASE WHEN scheduled_subscription_items.status = 'sent' THEN scheduled_subscription_items.status ELSE 'pending' END,
            last_error = NULL,
            updated_at = now()
        RETURNING *
        """,
        subscription_id, int(seq), file_kind, file_id, (copy_text or "")[:4000],
    )
    return dict(row)


async def remove_item(subscription_id: int, seq: int) -> bool:
    res = await db.execute(
        "DELETE FROM scheduled_subscription_items WHERE subscription_id = $1 AND seq = $2 AND status <> 'sent'",
        subscription_id, int(seq),
    )
    return res.endswith("1")


async def activate(subscription_id: int, start_at: datetime, send_time: str, scheduled_times: list[datetime]) -> dict | None:
    """يتحقق من اكتمال الأزواج ويحدد مواعيدها؛ يستدعيه service بعد التحقق."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            sub = await c.fetchrow("SELECT * FROM scheduled_subscriptions WHERE id = $1 FOR UPDATE", subscription_id)
            if not sub:
                return None
            rows = await c.fetch(
                "SELECT id, seq, status FROM scheduled_subscription_items WHERE subscription_id=$1 ORDER BY seq",
                subscription_id,
            )
            for r in rows:
                if r["status"] == "sent":
                    continue
                idx = int(r["seq"]) - 1
                if 0 <= idx < len(scheduled_times):
                    await c.execute(
                        "UPDATE scheduled_subscription_items SET scheduled_at=$2, status='pending', updated_at=now() WHERE id=$1",
                        r["id"], scheduled_times[idx],
                    )
            first = await c.fetchval(
                "SELECT min(scheduled_at) FROM scheduled_subscription_items WHERE subscription_id = $1 AND status = 'pending'",
                subscription_id,
            )
            row = await c.fetchrow(
                """
                UPDATE scheduled_subscriptions
                SET start_at = $2, send_time = $3::time, status = 'scheduled', next_send_at = $4,
                    sent_count = (SELECT count(*) FROM scheduled_subscription_items WHERE subscription_id=$1 AND status='sent'),
                    last_error = NULL, updated_at = now(), completed_at = NULL
                WHERE id = $1 RETURNING *
                """,
                subscription_id, start_at, _time_value(send_time), first,
            )
    return _row(row)


async def set_status(subscription_id: int, status: str) -> dict | None:
    row = await db.fetchrow(
        "UPDATE scheduled_subscriptions SET status = $2, updated_at = now() WHERE id = $1 RETURNING *",
        subscription_id, status,
    )
    return _row(row)


async def update_schedule(subscription_id: int, send_time: str, start_at: datetime, scheduled_times: list[datetime]) -> dict | None:
    async with db.pool().acquire() as c:
        async with c.transaction():
            sub = await c.fetchrow("SELECT * FROM scheduled_subscriptions WHERE id = $1 FOR UPDATE", subscription_id)
            if not sub:
                return None
            rows = await c.fetch(
                "SELECT id, seq, status FROM scheduled_subscription_items WHERE subscription_id = $1 ORDER BY seq",
                subscription_id,
            )
            for r in rows:
                if r["status"] == "sent":
                    continue
                idx = int(r["seq"]) - 1
                if 0 <= idx < len(scheduled_times):
                    await c.execute(
                        "UPDATE scheduled_subscription_items SET scheduled_at=$2, status='pending', updated_at=now() WHERE id=$1",
                        r["id"], scheduled_times[idx],
                    )
            first = await c.fetchval(
                "SELECT min(scheduled_at) FROM scheduled_subscription_items WHERE subscription_id=$1 AND status='pending'",
                subscription_id,
            )
            row = await c.fetchrow(
                """
                UPDATE scheduled_subscriptions
                SET send_time = $2::time, start_at=$3, next_send_at=$4,
                    status = CASE WHEN status='paused' THEN 'paused' ELSE 'scheduled' END,
                    updated_at=now()
                WHERE id=$1 RETURNING *
                """,
                subscription_id, _time_value(send_time), start_at, first,
            )
    return _row(row)


async def due_item(limit: int = 1) -> dict | None:
    """يحجز تسليماً واحداً حتى لا ترسله نسختان من scheduler معاً."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                """
                SELECT i.*, s.user_id, s.id AS subscription_id, s.package_title, s.total_items,
                       s.sent_count, s.status AS subscription_status
                FROM scheduled_subscription_items i
                JOIN scheduled_subscriptions s ON s.id = i.subscription_id
                WHERE s.status = 'scheduled'
                  AND ((i.status = 'pending' AND i.scheduled_at <= now())
                       OR (i.status = 'sending' AND i.updated_at < now() - interval '15 minutes'))
                ORDER BY i.scheduled_at NULLS LAST, i.id
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                max(1, min(int(limit), 10)),
            )
            if not row:
                return None
            await c.execute(
                "UPDATE scheduled_subscription_items SET status='sending', attempts=attempts+1, updated_at=now() WHERE id=$1",
                row["id"],
            )
            out = dict(row)
            out["attempts"] = int(row["attempts"] or 0) + 1
            return out


async def mark_sent(item_id: int, subscription_id: int) -> dict | None:
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                "UPDATE scheduled_subscription_items SET status='sent', sent_at=now(), last_error=NULL, updated_at=now() WHERE id=$1",
                item_id,
            )
            row = await c.fetchrow(
                """
                UPDATE scheduled_subscriptions s SET
                    sent_count = (SELECT count(*) FROM scheduled_subscription_items WHERE subscription_id=s.id AND status='sent'),
                    last_sent_at = now(),
                    next_send_at = (SELECT min(scheduled_at) FROM scheduled_subscription_items WHERE subscription_id=s.id AND status='pending'),
                    status = CASE WHEN NOT EXISTS (SELECT 1 FROM scheduled_subscription_items WHERE subscription_id=s.id AND status IN ('pending','sending','failed')) THEN 'completed' ELSE 'scheduled' END,
                    completed_at = CASE WHEN NOT EXISTS (SELECT 1 FROM scheduled_subscription_items WHERE subscription_id=s.id AND status IN ('pending','sending','failed')) THEN now() ELSE NULL END,
                    updated_at=now()
                WHERE s.id=$1 RETURNING *
                """,
                subscription_id,
            )
    return _row(row)


async def mark_failed(item_id: int, subscription_id: int, error: str, retry: bool) -> dict | None:
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                """
                UPDATE scheduled_subscription_items
                SET status=$2, last_error=$3, updated_at=now()
                WHERE id=$1
                """,
                item_id, "pending" if retry else "failed", (error or "")[:500],
            )
            row = await c.fetchrow(
                """
                UPDATE scheduled_subscriptions SET
                    status = CASE WHEN $2 THEN status ELSE 'paused' END,
                    last_error = $3, next_send_at = (SELECT min(scheduled_at) FROM scheduled_subscription_items WHERE subscription_id=$1 AND status='pending'),
                    updated_at=now()
                WHERE id=$1 RETURNING *
                """,
                subscription_id, retry, (error or "")[:500],
            )
    return _row(row)


async def due_subscriptions_count() -> int:
    return int(await db.fetchval("SELECT count(*) FROM scheduled_subscriptions WHERE status='scheduled' AND next_send_at <= now()") or 0)
