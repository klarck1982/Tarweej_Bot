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


class DuplicateOperation(Exception):
    """العملية نُفّذت مسبقاً بنفس المفتاح (ضغطة مكررة / زر قديم) — لا شيء تغيّر."""


async def lock_user(conn, user_id: int) -> Decimal:
    """يقفل صف المستخدم حتى نهاية المعاملة ويعيد رصيده.

    يجب أن يكون **أول** ما تفعله أي معاملة تمسّ الرصيد وتُنشئ صفوفاً مرتبطة بالمستخدم (orders…):
    الإدراج في جدول له مفتاح أجنبي إلى users يأخذ قفل KEY SHARE على صف المستخدم، فإن جاء
    FOR UPDATE بعده في معاملتين متزامنتين حدث deadlock. القفل أولاً = تسلسل نظيف بلا تعارض.
    """
    bal = await conn.fetchval("SELECT balance_usd FROM users WHERE tg_id = $1 FOR UPDATE", user_id)
    return Decimal(bal or 0)


async def claim_key(conn, key: str, user_id: int | None = None) -> None:
    """يحجز مفتاح «مرة واحدة فقط» داخل المعاملة الحالية. يرفع DuplicateOperation إن كان محجوزاً.

    إن فشلت المعاملة لاحقاً يُلغى الحجز معها — فيمكن إعادة المحاولة بنفس المفتاح.
    """
    got = await conn.fetchval(
        "INSERT INTO idempotency_keys (key, user_id) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING RETURNING key",
        key, user_id,
    )
    if got is None:
        raise DuplicateOperation(key)


async def credit(
    user_id: int,
    amount: Decimal,
    type_: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    note: str | None = None,
    admin_id: int | None = None,
    conn=None,
    idem_key: str | None = None,
) -> Decimal:
    """إضافة رصيد (شحن / استرداد / تعديل موجب). يعيد الرصيد الجديد.

    idem_key: إن مُرِّر لا تُنفَّذ العملية إلا مرة واحدة لهذا المفتاح (وإلا DuplicateOperation)."""
    amount = money(amount)
    if amount <= 0:
        raise ValueError("credit amount must be positive")

    async def _run(c) -> Decimal:
        async with c.transaction():
            await lock_user(c, user_id)
            if idem_key:
                await claim_key(c, idem_key, user_id)
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
    idem_key: str | None = None,
) -> Decimal:
    """خصم رصيد (طلب / تعديل سالب). يرفع InsufficientBalance إن لم يكفِ. يعيد الرصيد الجديد.

    idem_key: إن مُرِّر لا تُنفَّذ العملية إلا مرة واحدة لهذا المفتاح (وإلا DuplicateOperation)."""
    amount = money(amount)
    if amount <= 0:
        raise ValueError("debit amount must be positive")

    async def _run(c) -> Decimal:
        async with c.transaction():
            bal = await lock_user(c, user_id)
            if idem_key:
                await claim_key(c, idem_key, user_id)
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
