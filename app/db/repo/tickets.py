"""جدول 🎫 تذاكر الدعم ورسائلها."""

from __future__ import annotations

import json

from app.db import pool as db


OPEN_STATUSES = ("open", "answered")


def _row(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("admin_msg_ids"), str):
        try:
            d["admin_msg_ids"] = json.loads(d["admin_msg_ids"])
        except json.JSONDecodeError:
            d["admin_msg_ids"] = []
    return d


async def get(ticket_id: int) -> dict | None:
    row = await db.fetchrow(
        """
        SELECT t.*, u.name AS user_name, u.username AS user_username, u.balance_usd AS user_balance,
               o.status AS order_status, o.kind AS order_kind
        FROM tickets t
        JOIN users u ON u.tg_id = t.user_id
        LEFT JOIN orders o ON o.id = t.order_id
        WHERE t.id = $1
        """,
        ticket_id,
    )
    return _row(row)


async def messages(ticket_id: int, limit: int = 80) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM ticket_messages WHERE ticket_id = $1 ORDER BY id DESC LIMIT $2",
        ticket_id, limit,
    )
    return [dict(r) for r in reversed(rows)]


async def list_for_user(user_id: int, limit: int = 20) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT t.*, o.status AS order_status, o.kind AS order_kind
        FROM tickets t LEFT JOIN orders o ON o.id = t.order_id
        WHERE t.user_id = $1 ORDER BY COALESCE(t.last_msg_at, t.created_at) DESC, t.id DESC LIMIT $2
        """,
        user_id, limit,
    )
    return [_row(r) for r in rows]


async def list_admin(limit: int = 50) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT t.*, u.name AS user_name, u.username AS user_username, u.balance_usd AS user_balance,
               o.status AS order_status, o.kind AS order_kind
        FROM tickets t
        JOIN users u ON u.tg_id = t.user_id
        LEFT JOIN orders o ON o.id = t.order_id
        WHERE t.status <> 'closed'
        ORDER BY CASE WHEN t.status = 'open' THEN 0 ELSE 1 END,
                 COALESCE(t.last_msg_at, t.created_at) DESC, t.id DESC
        LIMIT $1
        """,
        limit,
    )
    return [_row(r) for r in rows]


async def count_open() -> int:
    return int(await db.fetchval("SELECT count(*) FROM tickets WHERE status = 'open'") or 0)


async def get_open_for(user_id: int, order_id: int | None = None, kind: str = "question") -> dict | None:
    if order_id is None:
        row = await db.fetchrow(
            "SELECT * FROM tickets WHERE user_id = $1 AND order_id IS NULL AND kind = $2 AND status <> 'closed' "
            "ORDER BY id DESC LIMIT 1",
            user_id, kind,
        )
    else:
        row = await db.fetchrow(
            "SELECT * FROM tickets WHERE user_id = $1 AND order_id = $2 AND status <> 'closed' "
            "ORDER BY id DESC LIMIT 1",
            user_id, order_id,
        )
    return _row(row)


async def create(user_id: int, order_id: int | None = None, topup_id: int | None = None,
                kind: str = "question") -> dict:
    existing = await get_open_for(user_id, order_id, kind)
    if existing:
        return existing
    row = await db.fetchrow(
        """
        INSERT INTO tickets (user_id, order_id, topup_id, kind, status, last_msg_at)
        VALUES ($1, $2, $3, $4, 'open', now()) RETURNING *
        """,
        user_id, order_id, topup_id, kind,
    )
    return _row(row)


async def add_message(ticket_id: int, sender_id: int, is_admin: bool, text: str | None = None,
                      file_id: str | None = None, file_kind: str | None = None) -> dict | None:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO ticket_messages (ticket_id, sender_id, is_admin, text, file_id, file_kind)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                ticket_id, sender_id, is_admin, text, file_id, file_kind,
            )
            status = "answered" if is_admin else "open"
            row = await conn.fetchrow(
                "UPDATE tickets SET status = $2, last_msg_at = now(), closed_at = NULL WHERE id = $1 RETURNING *",
                ticket_id, status,
            )
    return dict(row) if row else None


async def close(ticket_id: int, by_id: int | None = None) -> dict | None:
    row = await db.fetchrow(
        "UPDATE tickets SET status = 'closed', closed_at = now(), last_msg_at = now() WHERE id = $1 AND status <> 'closed' RETURNING *",
        ticket_id,
    )
    return dict(row) if row else None


async def auto_close(hours: int = 72) -> list[dict]:
    rows = await db.fetch(
        """
        UPDATE tickets SET status = 'closed', closed_at = now()
        WHERE status = 'answered' AND COALESCE(last_msg_at, created_at) < now() - ($1 || ' hours')::interval
        RETURNING id, user_id, order_id
        """,
        str(hours),
    )
    return [dict(r) for r in rows]


async def set_admin_messages(ticket_id: int, pairs: list[list[int]]) -> None:
    await db.execute("UPDATE tickets SET admin_msg_ids = $2::jsonb WHERE id = $1", ticket_id, json.dumps(pairs))


async def last_message(ticket_id: int) -> dict | None:
    row = await db.fetchrow("SELECT * FROM ticket_messages WHERE ticket_id = $1 ORDER BY id DESC LIMIT 1", ticket_id)
    return _row(row)
