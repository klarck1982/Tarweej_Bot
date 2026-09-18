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
                is_blocked_bot = FALSE,
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


async def admin_find(term: str) -> dict | None:
    """بحث الأدمن بـ Telegram ID أو @username أو #ORD-رقم مع ملخص الحساب."""
    raw = (term or "").strip()
    order_id: int | None = None
    if raw.lower().startswith("#ord"):
        try:
            order_id = int(raw.split("-")[-1].strip())
        except (ValueError, TypeError):
            return None
    user_id: int | None = None
    if order_id is None:
        try:
            user_id = int(raw.lstrip("@"))
        except (ValueError, TypeError):
            user_id = None
    # Telegram يرسل username بلا @ عادةً، لكن البيانات القديمة أو النسخ اليدوية
    # قد تحتوي @ أو مسافات/محارف خفية. نطبّع الطرفين حتى لا يفشل البحث
    # الصحيح بسبب اختلاف التخزين فقط.
    username = raw.strip().lstrip("@").strip().lower()
    if order_id is not None:
        where, args = "EXISTS (SELECT 1 FROM orders ox WHERE ox.user_id = u.tg_id AND ox.id = $1)", [order_id]
    elif user_id is not None:
        where, args = "u.tg_id = $1", [user_id]
    else:
        where = "regexp_replace(lower(COALESCE(u.username, '')), '[^a-z0-9_]', '', 'g') = regexp_replace($1, '[^a-z0-9_]', '', 'g')"
        args = [username]
    row = await db.fetchrow(
        f"""
        SELECT u.*,
          (SELECT count(*) FROM orders o WHERE o.user_id = u.tg_id) AS order_count,
          (SELECT count(*) FROM orders o WHERE o.user_id = u.tg_id
             AND o.status IN ('paid','submitted','in_progress','active','paused','needs_revision','delivered')) AS open_orders,
          (SELECT count(*) FROM tickets t WHERE t.user_id = u.tg_id AND t.status <> 'closed') AS open_tickets,
          (SELECT count(*) FROM ledger l WHERE l.user_id = u.tg_id AND l.type = 'topup' AND l.amount_usd > 0) AS approved_topups,
          (SELECT COALESCE(sum(l.amount_usd), 0) FROM ledger l WHERE l.user_id = u.tg_id) AS ledger_total
        FROM users u WHERE {where} LIMIT 1
        """,
        *args,
    )
    return dict(row) if row else None


def _broadcast_extra(segment: str) -> str:
    if segment == "balance":
        return " AND u.balance_usd > 0"
    if segment == "ordered":
        return " AND EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.tg_id AND o.paid_at IS NOT NULL AND o.status <> 'cancelled')"
    if segment == "new":
        return " AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.tg_id AND o.paid_at IS NOT NULL)"
    return ""


async def broadcast_counts() -> dict[str, int]:
    base = "FROM users u WHERE u.accepted_terms_at IS NOT NULL AND NOT u.is_blocked AND NOT u.is_blocked_bot"
    row = await db.fetchrow(
        "SELECT "
        "(SELECT count(*) " + base + ") AS all_users, "
        "(SELECT count(*) " + base + " AND u.balance_usd > 0) AS balance, "
        "(SELECT count(*) " + base + " AND EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.tg_id AND o.paid_at IS NOT NULL AND o.status <> 'cancelled')) AS ordered, "
        "(SELECT count(*) " + base + " AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.tg_id AND o.paid_at IS NOT NULL)) AS new"
    )
    return {"all": int(row["all_users"] or 0), "balance": int(row["balance"] or 0),
            "ordered": int(row["ordered"] or 0), "new": int(row["new"] or 0)}


async def broadcast_recipients(segment: str = "all") -> list[dict]:
    """مستلمو البث مع الاسم لاستبدال {name}."""
    rows = await db.fetch(
        "SELECT u.tg_id, u.name FROM users u "
        "WHERE u.accepted_terms_at IS NOT NULL AND NOT u.is_blocked AND NOT u.is_blocked_bot" + _broadcast_extra(segment) + " ORDER BY u.tg_id"
    )
    return [{"tg_id": int(r["tg_id"]), "name": r["name"] or "صديقنا"} for r in rows]


async def broadcast_targets(segment: str = "all") -> list[int]:
    return [x["tg_id"] for x in await broadcast_recipients(segment)]


async def mark_bot_blocked(tg_id: int, blocked: bool = True) -> None:
    await db.execute("UPDATE users SET is_blocked_bot = $2 WHERE tg_id = $1", tg_id, blocked)


async def set_blocked(tg_id: int, blocked: bool) -> None:
    await db.execute("UPDATE users SET is_blocked = $2 WHERE tg_id = $1", tg_id, blocked)
