"""كل ما يخص جدول users."""

from __future__ import annotations

from decimal import Decimal

from app.db import pool as db


async def upsert_user(tg_id: int, name: str, username: str | None, referred_by: int | None = None) -> bool:
    """يسجّل المستخدم إن كان جديداً ويحدّث اسمه/آخر ظهور دائماً. يعيد True إن كان جديداً."""
    row = await db.fetchrow(
        """
        INSERT INTO users (tg_id, name, username, referred_by)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tg_id) DO UPDATE
            SET name = EXCLUDED.name,
                username = EXCLUDED.username,
                last_seen = now()
        RETURNING (xmax = 0) AS is_new
        """,
        tg_id, name[:120], username, referred_by,
    )
    return bool(row and row["is_new"])


async def touch(tg_id: int) -> None:
    await db.execute("UPDATE users SET last_seen = now() WHERE tg_id = $1", tg_id)


async def get_user(tg_id: int):
    return await db.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)


async def get_balance(tg_id: int) -> Decimal:
    val = await db.fetchval("SELECT balance_usd FROM users WHERE tg_id = $1", tg_id)
    return Decimal(val) if val is not None else Decimal("0")


async def accept_terms(tg_id: int) -> None:
    await db.execute(
        "UPDATE users SET accepted_terms_at = COALESCE(accepted_terms_at, now()) WHERE tg_id = $1", tg_id
    )


async def has_accepted_terms(tg_id: int) -> bool:
    return bool(await db.fetchval("SELECT accepted_terms_at IS NOT NULL FROM users WHERE tg_id = $1", tg_id))


async def is_blocked(tg_id: int) -> bool:
    return bool(await db.fetchval("SELECT is_blocked FROM users WHERE tg_id = $1", tg_id))


async def count_users() -> int:
    return int(await db.fetchval("SELECT count(*) FROM users") or 0)
