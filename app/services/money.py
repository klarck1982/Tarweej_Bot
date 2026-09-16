"""القواعد المالية الثلاث — كل تغيير في الرصيد يمرّ من هنا حصراً.

1) معاملة واحدة: سطر في ledger + تحديث users.balance_usd معاً أو لا شيء.
2) SELECT ... FOR UPDATE على صف المستخدم — يمنع الضغط المزدوج السريع.
3) كل مبلغ Decimal لا float.
"""

from __future__ import annotations

from decimal import Decimal

from app.db import pool as db
from app.services.pricing import money


class InsufficientBalance(Exception):
    def __init__(self, balance: Decimal, needed: Decimal) -> None:
        self.balance = balance
        self.needed = needed
        super().__init__(f"balance {balance} < needed {needed}")


async def credit(
    user_id: int,
    amount: Decimal,
    type_: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    note: str | None = None,
    admin_id: int | None = None,
    conn=None,
) -> Decimal:
    """إضافة رصيد (شحن / استرداد / تعديل موجب). يعيد الرصيد الجديد."""
    amount = money(amount)
    if amount <= 0:
        raise ValueError("credit amount must be positive")

    async def _run(c) -> Decimal:
        async with c.transaction():
            await c.execute("SELECT 1 FROM users WHERE tg_id = $1 FOR UPDATE", user_id)
            await c.execute(
                "INSERT INTO ledger (user_id, type, amount_usd, ref_type, ref_id, note, admin_id) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                user_id, type_, amount, ref_type, ref_id, note, admin_id,
            )
            new_balance = await c.fetchval(
                "UPDATE users SET balance_usd = balance_usd + $2 WHERE tg_id = $1 RETURNING balance_usd", user_id, amount
            )
            return Decimal(new_balance)

    if conn is not None:
        return await _run(conn)
    async with db.pool().acquire() as c:
        return await _run(c)


async def debit(
    user_id: int,
    amount: Decimal,
    type_: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    note: str | None = None,
    admin_id: int | None = None,
    conn=None,
) -> Decimal:
    """خصم رصيد (طلب / تعديل سالب). يرفع InsufficientBalance إن لم يكفِ. يعيد الرصيد الجديد."""
    amount = money(amount)
    if amount <= 0:
        raise ValueError("debit amount must be positive")

    async def _run(c) -> Decimal:
        async with c.transaction():
            bal = await c.fetchval("SELECT balance_usd FROM users WHERE tg_id = $1 FOR UPDATE", user_id)
            bal = Decimal(bal or 0)
            if bal < amount:
                raise InsufficientBalance(bal, amount)
            await c.execute(
                "INSERT INTO ledger (user_id, type, amount_usd, ref_type, ref_id, note, admin_id) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                user_id, type_, -amount, ref_type, ref_id, note, admin_id,
            )
            new_balance = await c.fetchval(
                "UPDATE users SET balance_usd = balance_usd - $2 WHERE tg_id = $1 RETURNING balance_usd", user_id, amount
            )
            return Decimal(new_balance)

    if conn is not None:
        return await _run(conn)
    async with db.pool().acquire() as c:
        return await _run(c)


async def ledger_sum(user_id: int) -> Decimal:
    """مجموع الدفتر — المرجع عند أي خلاف؛ يجب أن يساوي users.balance_usd دائماً."""
    v = await db.fetchval("SELECT COALESCE(sum(amount_usd), 0) FROM ledger WHERE user_id = $1", user_id)
    return Decimal(v)
