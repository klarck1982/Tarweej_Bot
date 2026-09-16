"""جدول topups: طلبات الشحن من الإنشاء حتى قرار الأدمن."""

from __future__ import annotations

import json
from decimal import Decimal

from app.db import pool as db
from app.services import money as money_svc
from app.services.pricing import money


async def get_pending_for_user(user_id: int):
    return await db.fetchrow(
        "SELECT * FROM topups WHERE user_id = $1 AND status = 'pending' ORDER BY id DESC LIMIT 1", user_id
    )


async def create(user_id: int, method: str, amount_usd: Decimal):
    """ينشئ طلب شحن معلّقاً. إن وُجد طلب معلّق سابق يُلغى تلقائياً (طلب واحد معلّق لكل مستخدم)."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                "UPDATE topups SET status = 'cancelled', decided_at = now(), reason = 'استُبدل بطلب جديد' "
                "WHERE user_id = $1 AND status = 'pending'",
                user_id,
            )
            return await c.fetchrow(
                "INSERT INTO topups (user_id, method, amount_usd) VALUES ($1, $2, $3) RETURNING *",
                user_id, method, money(amount_usd),
            )


async def attach_proof(topup_id: int, file_id: str | None, text: str | None) -> None:
    await db.execute(
        "UPDATE topups SET proof_file_id = COALESCE($2, proof_file_id), proof_text = COALESCE($3, proof_text) WHERE id = $1",
        topup_id, file_id, text,
    )


async def set_messages(topup_id: int, admin_msg_ids: list[list[int]] | None = None, user_msg_id: int | None = None) -> None:
    await db.execute(
        "UPDATE topups SET admin_msg_ids = COALESCE($2::jsonb, admin_msg_ids), user_msg_id = COALESCE($3, user_msg_id) WHERE id = $1",
        topup_id, json.dumps(admin_msg_ids) if admin_msg_ids is not None else None, user_msg_id,
    )


async def cancel_by_user(topup_id: int, user_id: int) -> bool:
    res = await db.execute(
        "UPDATE topups SET status = 'cancelled', decided_at = now(), reason = 'ألغاه المستخدم' "
        "WHERE id = $1 AND user_id = $2 AND status = 'pending'",
        topup_id, user_id,
    )
    return res.endswith("1")


async def get(topup_id: int):
    return await db.fetchrow(
        "SELECT t.*, u.name AS user_name, u.username AS user_username, u.balance_usd AS user_balance "
        "FROM topups t JOIN users u ON u.tg_id = t.user_id WHERE t.id = $1",
        topup_id,
    )


async def list_pending(limit: int = 20):
    return await db.fetch(
        "SELECT t.*, u.name AS user_name, u.username AS user_username FROM topups t JOIN users u ON u.tg_id = t.user_id "
        "WHERE t.status = 'pending' ORDER BY t.id ASC LIMIT $1",
        limit,
    )


async def count_pending() -> int:
    return int(await db.fetchval("SELECT count(*) FROM topups WHERE status = 'pending'") or 0)


async def approve(topup_id: int, admin_id: int, amount_override: Decimal | None = None) -> tuple[bool, Decimal | None, dict | None]:
    """يعتمد الشحن ويضيف الرصيد في معاملة واحدة.

    يعيد (نجح؟, الرصيد الجديد, بيانات الطلب). لا يعتمد إلا إن كان pending — يمنع الاعتماد المزدوج.
    """
    async with db.pool().acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "UPDATE topups SET status = 'approved', admin_id = $2, decided_at = now(), "
                "amount_usd = COALESCE($3, amount_usd) "
                "WHERE id = $1 AND status = 'pending' RETURNING *",
                topup_id, admin_id, money(amount_override) if amount_override is not None else None,
            )
            if row is None:
                return False, None, None
            new_balance = await money_svc.credit(
                row["user_id"], Decimal(row["amount_usd"]), "topup",
                ref_type="topup", ref_id=topup_id, note=f"TOP-{topup_id} {row['method']}", admin_id=admin_id, conn=c,
            )
            return True, new_balance, dict(row)


async def reject(topup_id: int, admin_id: int, reason: str) -> dict | None:
    row = await db.fetchrow(
        "UPDATE topups SET status = 'rejected', admin_id = $2, decided_at = now(), reason = $3 "
        "WHERE id = $1 AND status = 'pending' RETURNING *",
        topup_id, admin_id, reason[:300],
    )
    return dict(row) if row else None


async def stats_today() -> dict:
    row = await db.fetchrow(
        "SELECT COALESCE(sum(amount_usd) FILTER (WHERE status='approved' AND decided_at >= date_trunc('day', now())), 0) AS approved_today, "
        "count(*) FILTER (WHERE status='pending') AS pending FROM topups"
    )
    return dict(row)
