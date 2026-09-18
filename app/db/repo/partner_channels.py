"""جدول partner_channels — القنوات الشريكة التي يديرها الأدمن من Cpanel.

القناة «حيّة» = enabled و غير مؤرشفة → تظهر للعميل. الإيقاف يخفيها فوراً؛ الطلبات الجارية تكمل لأن الطلب يحمل نسخة
من اسم القناة وسعرها لحظة الدفع في spec.
"""

from __future__ import annotations

from decimal import Decimal

from app.db import pool as db
from app.services.pricing import money

CATEGORIES: dict[str, tuple[str, str]] = {
    "shopping": ("🛍️", "تسوق ومتاجر"),
    "tech":     ("💻", "تقنية"),
    "news":     ("📰", "أخبار"),
    "fun":      ("🎬", "ترفيه"),
    "edu":      ("📚", "تعليم"),
    "local":    ("🍽️", "مطاعم ومحلي"),
    "general":  ("📢", "عام"),
}
FIELDS = ("title", "username", "url", "category", "subscribers", "avg_views", "blurb", "price_24h", "price_48h",
          "price_pin", "allow_pin", "owner_contact", "notes", "enabled", "sort_order")


def _d(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("price_24h", "price_48h", "price_pin"):
        if d.get(k) is not None:
            d[k] = money(Decimal(d[k]))
    return d


def cat_label(code: str) -> str:
    e, name = CATEGORIES.get(code, CATEGORIES["general"])
    return f"{e} {name}"


async def list_all(include_archived: bool = False) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM partner_channels WHERE ($1 OR NOT archived) ORDER BY sort_order, subscribers DESC, id", include_archived)
    return [_d(r) for r in rows]


async def list_live(category: str | None = None) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM partner_channels WHERE enabled AND NOT archived AND ($1::text IS NULL OR category = $1) "
        "ORDER BY sort_order, subscribers DESC, id", category)
    return [_d(r) for r in rows]


async def count_live() -> int:
    return int(await db.fetchval("SELECT count(*) FROM partner_channels WHERE enabled AND NOT archived") or 0)


async def live_categories() -> list[tuple[str, int]]:
    """الفئات التي فيها قنوات حيّة مع العدد — بترتيب CATEGORIES."""
    rows = await db.fetch("SELECT category, count(*) AS n FROM partner_channels WHERE enabled AND NOT archived GROUP BY 1")
    counts = {r["category"]: int(r["n"]) for r in rows}
    return [(c, counts[c]) for c in CATEGORIES if counts.get(c)]


async def get(channel_id: int) -> dict | None:
    return _d(await db.fetchrow("SELECT * FROM partner_channels WHERE id = $1", channel_id))


async def min_live_price_24h() -> Decimal | None:
    v = await db.fetchval("SELECT min(price_24h) FROM partner_channels WHERE enabled AND NOT archived")
    return money(Decimal(v)) if v is not None else None


async def create(data: dict) -> dict:
    cols = [k for k in FIELDS if k in data]
    vals = [data[k] for k in cols]
    ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
    row = await db.fetchrow(f"INSERT INTO partner_channels ({', '.join(cols)}) VALUES ({ph}) RETURNING *", *vals)
    return _d(row)


async def update(channel_id: int, data: dict) -> dict | None:
    cols = [k for k in FIELDS if k in data]
    if not cols:
        return await get(channel_id)
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(cols))
    row = await db.fetchrow(f"UPDATE partner_channels SET {sets}, updated_at = now() WHERE id = $1 RETURNING *",
                            channel_id, *[data[k] for k in cols])
    return _d(row)


async def delete_or_archive(channel_id: int) -> str:
    """حذف نهائي إن لم يكن للقناة طلبات، وإلا أرشفة. يعيد 'deleted' | 'archived' | 'missing'."""
    used = await db.fetchval("SELECT count(*) FROM orders WHERE kind = 'tg_post' AND (spec->>'channel_id')::bigint = $1", channel_id)
    if int(used or 0) == 0:
        res = await db.execute("DELETE FROM partner_channels WHERE id = $1", channel_id)
        return "deleted" if res.endswith("1") else "missing"
    res = await db.execute("UPDATE partner_channels SET archived = TRUE, enabled = FALSE, updated_at = now() WHERE id = $1", channel_id)
    return "archived" if res.endswith("1") else "missing"


async def month_stats() -> dict:
    """منشورات هذا الشهر: العدد، الإيراد، الربح بعد تكلفة القناة (المستردّة لا تُحسب)."""
    row = await db.fetchrow(
        "SELECT count(*) FILTER (WHERE status NOT IN ('rejected','refunded','cancelled')) AS n, "
        "coalesce(sum(price_usd - refunded_usd), 0) AS revenue, "
        "coalesce(sum(CASE WHEN status IN ('rejected','refunded','cancelled') THEN 0 ELSE cost_usd END), 0) AS cost "
        "FROM orders WHERE kind = 'tg_post' AND status <> 'awaiting_payment' AND paid_at >= date_trunc('month', now())")
    rev, cost = Decimal(row["revenue"]), Decimal(row["cost"])
    return {"posts": int(row["n"]), "revenue": float(rev), "profit": float(rev - cost)}
