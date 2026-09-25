"""جدول orders (+ order_media) — الطلبات من المسودة حتى الاكتمال.

حالاتنا (status):
    awaiting_payment  مسودة محفوظة بانتظار شحن الرصيد (واحدة لكل مستخدم، تنتهي بعد 7 أيام)
    paid              خُصم من العميل ولم يصل لنور بعد (قيد الإرسال / إعادة محاولة)
    submitted         وصل لنور — pending_admin عندهم
    in_progress       نور قَبِل — مدير الحملة يجهّز ويتواصل مع العميل
    active            الإعلان يعمل
    paused            متوقف مؤقتاً
    completed         انتهى
    rejected          نور رفض — أُعيد المبلغ للعميل
    failed_submit     تعذّر الإرسال نهائياً — أُعيد المبلغ
    refunded          أُعيد المبلغ يدوياً من الأدمن
    cancelled         مسودة ألغيت/انتهت صلاحيتها
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.db import pool as db
from app.services.pricing import money

OPEN_STATUSES = ("paid", "submitted", "in_progress", "active", "paused", "needs_revision", "delivered")
FINAL_STATUSES = ("completed", "rejected", "failed_submit", "refunded", "cancelled")
JSON_COLS = ("nour_payload", "nour_response", "spec", "results", "delivery", "channel_msg_ids")
KINDS = ("meta_campaign", "tg_ads", "tg_post", "copy", "design", "reel", "montage", "bundle")


def _j(v):
    """asyncpg يعيد JSONB كنص أحياناً."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def row_to_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("spec", "nour_payload", "nour_response", "admin_msg_ids", "results", "delivery", "channel_msg_ids"):
        if k in d:
            d[k] = _j(d[k]) or ({} if k not in ("admin_msg_ids", "delivery", "channel_msg_ids") else [])
    return d


async def get(order_id: int) -> dict | None:
    row = await db.fetchrow(
        "SELECT o.*, u.name AS user_name, u.username AS user_username, u.balance_usd AS user_balance "
        "FROM orders o JOIN users u ON u.tg_id = o.user_id WHERE o.id = $1",
        order_id,
    )
    return row_to_dict(row)


async def get_awaiting(user_id: int) -> dict | None:
    row = await db.fetchrow(
        "SELECT * FROM orders WHERE user_id = $1 AND status = 'awaiting_payment' ORDER BY id DESC LIMIT 1", user_id
    )
    return row_to_dict(row)


async def list_for_user(user_id: int, limit: int = 10, offset: int = 0) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM orders WHERE user_id = $1 AND status <> 'cancelled' ORDER BY id DESC LIMIT $2 OFFSET $3",
        user_id, limit, offset,
    )
    return [row_to_dict(r) for r in rows]


async def count_for_user(user_id: int) -> int:
    return int(await db.fetchval("SELECT count(*) FROM orders WHERE user_id = $1 AND status <> 'cancelled'", user_id) or 0)


async def list_open(limit: int = 30) -> list[dict]:
    rows = await db.fetch(
        "SELECT o.*, u.name AS user_name, u.username AS user_username FROM orders o JOIN users u ON u.tg_id = o.user_id "
        "WHERE o.status = ANY($1::text[]) ORDER BY o.id DESC LIMIT $2",
        list(OPEN_STATUSES), limit,
    )
    return [row_to_dict(r) for r in rows]


async def count_open() -> int:
    return int(await db.fetchval("SELECT count(*) FROM orders WHERE status = ANY($1::text[])", list(OPEN_STATUSES)) or 0)


async def count_attention(dry_run: bool) -> int:
    """ما ينتظر تدخّل الأدمن: عالق قبل نور، (في المحاكاة) ينتظر «قرار نور»، وكل طلب تيليغرام يدوي مفتوح."""
    statuses = ["paid"] + (["submitted", "in_progress", "active"] if dry_run else [])
    meta = int(await db.fetchval("SELECT count(*) FROM orders WHERE kind = 'meta_campaign' AND status = ANY($1::text[])", statuses) or 0)
    manual = int(await db.fetchval(
        "SELECT count(*) FROM orders WHERE kind <> 'meta_campaign' AND status IN ('paid','submitted','in_progress','active') "
        "AND NOT (kind = 'design' AND status = 'in_progress') "
        "AND NOT (kind = 'tg_post' AND owner_user_id IS NOT NULL AND spec->>'text' IS NOT NULL)") or 0)
    design_rev = int(await db.fetchval("SELECT count(*) FROM orders WHERE kind = 'design' AND status = 'needs_revision'") or 0)
    return meta + manual + design_rev


async def list_tasks(limit: int = 30) -> list[dict]:
    """🛠️ لوحة المهام: كل ما ينتظر يد الأدمن (تصميم/قنوات/Telegram Ads) — الأقرب موعداً أولاً."""
    rows = await db.fetch(
        "SELECT o.*, u.name AS user_name, u.username AS user_username FROM orders o JOIN users u ON u.tg_id = o.user_id "
        "WHERE (o.kind = 'design' AND o.status IN ('submitted','in_progress','needs_revision')) "
        "   OR (o.kind = 'tg_post' AND o.status IN ('submitted','in_progress') "
        "       AND NOT (o.owner_user_id IS NOT NULL AND o.spec->>'text' IS NOT NULL)) "
        "   OR (o.kind = 'tg_ads' AND o.status IN ('submitted','in_progress')) "
        "ORDER BY COALESCE(o.due_at, o.scheduled_at, o.paid_at + interval '24 hours', o.created_at) NULLS LAST, o.id LIMIT $1", limit)
    return [row_to_dict(r) for r in rows]


async def save_awaiting(user_id: int, spec: dict, price: Decimal, cost: Decimal, days_valid: int,
                        order_id: int | None = None, kind: str = "meta_campaign") -> dict:
    """يحفظ/يحدّث المسودة بانتظار الدفع. مسودة واحدة لكل مستخدم — القديمة تُلغى."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                "UPDATE orders SET status = 'cancelled', note = 'استُبدلت بمسودة أحدث', updated_at = now() "
                "WHERE user_id = $1 AND status = 'awaiting_payment' AND ($2::bigint IS NULL OR id <> $2)",
                user_id, order_id,
            )
            if order_id:
                row = await c.fetchrow(
                    "UPDATE orders SET spec = $2::jsonb, price_usd = $3, cost_usd = $4, kind = $7, "
                    "expires_at = now() + ($5 || ' days')::interval, updated_at = now() "
                    "WHERE id = $1 AND user_id = $6 AND status = 'awaiting_payment' RETURNING *",
                    order_id, json.dumps(spec, ensure_ascii=False), money(price), money(cost), str(days_valid), user_id, kind,
                )
                if row:
                    return row_to_dict(row)
            row = await c.fetchrow(
                "INSERT INTO orders (user_id, kind, status, spec, price_usd, cost_usd, expires_at) "
                "VALUES ($1, $6, 'awaiting_payment', $2::jsonb, $3, $4, now() + ($5 || ' days')::interval) RETURNING *",
                user_id, json.dumps(spec, ensure_ascii=False), money(price), money(cost), str(days_valid), kind,
            )
            return row_to_dict(row)


async def cancel_awaiting(order_id: int, user_id: int, note: str = "ألغاها المستخدم") -> bool:
    res = await db.execute(
        "UPDATE orders SET status = 'cancelled', note = $3, updated_at = now() "
        "WHERE id = $1 AND user_id = $2 AND status = 'awaiting_payment'",
        order_id, user_id, note,
    )
    return res.endswith("1")


async def add_media(order_id: int, items: list[tuple[str, str]]) -> None:
    if not items:
        return
    async with db.pool().acquire() as c:
        await c.executemany(
            "INSERT INTO order_media (order_id, kind, file_id) VALUES ($1, $2, $3)",
            [(order_id, k, f) for k, f in items],
        )


async def media(order_id: int) -> list[dict]:
    rows = await db.fetch("SELECT kind, file_id FROM order_media WHERE order_id = $1 ORDER BY id", order_id)
    return [dict(r) for r in rows]


async def set_messages(order_id: int, admin_msg_ids: list[list[int]] | None = None, user_msg_id: int | None = None) -> None:
    await db.execute(
        "UPDATE orders SET admin_msg_ids = COALESCE($2::jsonb, admin_msg_ids), user_msg_id = COALESCE($3, user_msg_id) WHERE id = $1",
        order_id, json.dumps(admin_msg_ids) if admin_msg_ids is not None else None, user_msg_id,
    )


async def update(order_id: int, **fields) -> dict | None:
    """تحديث أعمدة بسيطة: status, nour_id, nour_status, note, charged_usd, next_retry_at, ..."""
    if not fields:
        return await get(order_id)
    sets, args = [], [order_id]
    for k, v in fields.items():
        is_json = k in JSON_COLS
        args.append(json.dumps(v, ensure_ascii=False, default=str) if is_json else v)
        cast = "::jsonb" if is_json else ""
        sets.append(f"{k} = ${len(args)}{cast}")
    sets.append("updated_at = now()")
    row = await db.fetchrow(f"UPDATE orders SET {', '.join(sets)} WHERE id = $1 RETURNING *", *args)
    return row_to_dict(row)


async def transition(order_id: int, from_statuses, **fields) -> dict | None:
    """انتقال ذري: يحدّث فقط إن كانت الحالة الحالية ضمن from_statuses — وإلا None (غيّرها طرف آخر للتو).

    يمنع نمط «اقرأ ثم اكتب» من الكتابة فوق استرداد/إلغاء/تعديل حدث في اللحظة نفسها."""
    if isinstance(from_statuses, str):
        from_statuses = (from_statuses,)
    sets, args = [], [order_id, list(from_statuses)]
    for k, v in fields.items():
        is_json = k in JSON_COLS
        args.append(json.dumps(v, ensure_ascii=False, default=str) if is_json else v)
        cast = "::jsonb" if is_json else ""
        sets.append(f"{k} = ${len(args)}{cast}")
    sets.append("updated_at = now()")
    row = await db.fetchrow(
        f"UPDATE orders SET {', '.join(sets)} WHERE id = $1 AND status = ANY($2::text[]) RETURNING *", *args)
    return row_to_dict(row)


async def wake_waiting_username() -> int:
    """بعد ضبط المعرّف الاحتياطي: الطلبات المنتظرة بسببه تُعاد في الدورة التالية للمجدول بدل انتظار 30 دقيقة."""
    res = await db.execute(
        "UPDATE orders SET next_retry_at = now(), updated_at = now() WHERE kind = 'meta_campaign' AND status = 'paid' "
        "AND note LIKE '%المعرّف الاحتياطي غير مضبوط%'")
    return int(res.split()[-1]) if res else 0


async def due_for_retry(limit: int = 10) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM orders WHERE kind = 'meta_campaign' AND status = 'paid' "
        "AND (next_retry_at IS NULL OR next_retry_at <= now()) "
        "AND (submitting_until IS NULL OR submitting_until < now()) "
        "ORDER BY id LIMIT $1",
        limit,
    )
    return [row_to_dict(r) for r in rows]


async def expire_drafts() -> int:
    res = await db.execute(
        "UPDATE orders SET status = 'cancelled', note = 'انتهت صلاحية المسودة', updated_at = now() "
        "WHERE status = 'awaiting_payment' AND expires_at < now()"
    )
    try:
        return int(res.split()[-1])
    except Exception:  # noqa: BLE001
        return 0


async def stats_today() -> dict:
    row = await db.fetchrow(
        "SELECT count(*) FILTER (WHERE paid_at >= date_trunc('day', now())) AS orders_today, "
        "COALESCE(sum(price_usd) FILTER (WHERE paid_at >= date_trunc('day', now()) AND status <> 'awaiting_payment'), 0) AS sales_today, "
        "count(*) FILTER (WHERE status = ANY($1::text[])) AS open FROM orders",
        list(OPEN_STATUSES),
    )
    return dict(row)
