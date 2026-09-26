"""📅 طابور باقة التصميم اليومي — طبقة قاعدة البيانات.

النموذج: كل اشتراك طابور أزواج (seq تلقائي FIFO) · وجه تسليم (محادثة/قناة) · إرسال واحد يومياً في الساعة.
كل انتقال حالة **مشروط** (لا كتابة فوق refunded/cancelled/completed) — وحجز ذرّي للعناصر يمنع الإرسال المزدوج.
"""

from __future__ import annotations

from datetime import datetime, time

from app.db import pool as db

ACTIVE_STATES = ("awaiting_assets", "scheduled", "paused")
FINAL_STATES = ("completed", "cancelled", "refunded")


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


def _row(row) -> dict | None:
    return dict(row) if row else None


# ───────────────── الاشتراكات ─────────────────

async def create_sub(*, user_id: int | None, order_id: int | None, package_code: str, package_title: str,
                     price_usd, total_items: int, duration_days: int, send_time, timezone_name: str,
                     target_chat_id: int, target_kind: str, target_title: str | None,
                     customer_name: str | None, is_manual: bool, note: str | None,
                     checkout_key: str | None, next_send_at: datetime | None, conn=None) -> dict:
    run = conn.fetchrow if conn is not None else db.fetchrow
    row = await run(
        """
        INSERT INTO scheduled_subscriptions
            (user_id, order_id, package_code, package_title, price_usd, total_items, duration_days,
             send_time, timezone, target_chat_id, target_kind, target_title, customer_name,
             is_manual, note, checkout_key, status, next_send_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::time,$9,$10,$11,$12,$13,$14,$15,$16,'scheduled',$17)
        RETURNING *
        """,
        user_id, order_id, package_code, package_title, price_usd, int(total_items), int(duration_days),
        _time_value(send_time), timezone_name, int(target_chat_id), target_kind, target_title,
        customer_name, bool(is_manual), note, checkout_key, next_send_at,
    )
    return dict(row)


async def get_subscription(subscription_id: int) -> dict | None:
    row = await db.fetchrow(
        """
        SELECT s.*, u.name AS user_name, u.username AS user_username, u.balance_usd AS user_balance,
               (SELECT count(*) FROM scheduled_subscription_items i
                 WHERE i.subscription_id = s.id AND i.status = 'sent') AS sent_items,
               (SELECT count(*) FROM scheduled_subscription_items i
                 WHERE i.subscription_id = s.id AND i.status IN ('pending','sending')) AS ready_items
        FROM scheduled_subscriptions s
        LEFT JOIN users u ON u.tg_id = s.user_id
        WHERE s.id = $1
        """,
        subscription_id,
    )
    return _row(row)


async def find_by_checkout(checkout_key: str, user_id: int) -> int | None:
    return await db.fetchval(
        "SELECT id FROM scheduled_subscriptions WHERE checkout_key=$1 AND user_id=$2", checkout_key, user_id)


async def list_subscriptions(query: str = "", limit: int = 40, offset: int = 0) -> list[dict]:
    """للأدمن — بحث عام (اسم/وجه/رقم اشتراك)."""
    q = (query or "").strip().lstrip("@").lower()
    rows = await db.fetch(
        """
        SELECT s.*, u.name AS user_name, u.username AS user_username,
               (SELECT count(*) FROM scheduled_subscription_items i
                 WHERE i.subscription_id = s.id AND i.status = 'sent') AS sent_items,
               (SELECT count(*) FROM scheduled_subscription_items i
                 WHERE i.subscription_id = s.id AND i.status IN ('pending','sending')) AS ready_items
        FROM scheduled_subscriptions s
        LEFT JOIN users u ON u.tg_id = s.user_id
        WHERE ($1 = '' OR lower(coalesce(s.customer_name,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(s.target_title,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(u.name,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(u.username,'')) LIKE '%' || $1 || '%'
               OR s.user_id::text = $1 OR s.id::text = $1
               OR lower(s.package_title) LIKE '%' || $1 || '%')
        ORDER BY s.id DESC
        LIMIT $2 OFFSET $3
        """,
        q, max(1, min(int(limit), 100)), max(0, int(offset)),
    )
    return [dict(r) for r in rows]


async def list_for_user(user_id: int, limit: int = 10) -> list[dict]:
    """اشتراكات مستخدم واحد حصراً (لا بحث عام — خصوصية + ترقيم صحيح)."""
    rows = await db.fetch(
        """
        SELECT s.*,
               (SELECT count(*) FROM scheduled_subscription_items i
                 WHERE i.subscription_id = s.id AND i.status = 'sent') AS sent_items,
               (SELECT count(*) FROM scheduled_subscription_items i
                 WHERE i.subscription_id = s.id AND i.status IN ('pending','sending')) AS ready_items
        FROM scheduled_subscriptions s
        WHERE s.user_id = $1
        ORDER BY s.id DESC
        LIMIT $2
        """,
        int(user_id), max(1, min(int(limit), 50)),
    )
    return [dict(r) for r in rows]


async def count_subscriptions(query: str = "") -> int:
    q = (query or "").strip().lstrip("@").lower()
    return int(await db.fetchval(
        """
        SELECT count(*) FROM scheduled_subscriptions s
        LEFT JOIN users u ON u.tg_id = s.user_id
        WHERE ($1 = '' OR lower(coalesce(s.customer_name,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(s.target_title,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(u.name,'')) LIKE '%' || $1 || '%'
               OR lower(coalesce(u.username,'')) LIKE '%' || $1 || '%'
               OR s.user_id::text = $1 OR s.id::text = $1
               OR lower(s.package_title) LIKE '%' || $1 || '%')
        """,
        q,
    ) or 0)


async def active_on_target(target_chat_id: int) -> dict | None:
    """اشتراك نشط مربوط بهذا الوجه (حماية من الربط المزدوج/الخلط)."""
    return _row(await db.fetchrow(
        "SELECT * FROM scheduled_subscriptions WHERE target_chat_id=$1 AND status = ANY($2::text[]) LIMIT 1",
        int(target_chat_id), list(ACTIVE_STATES)))


async def update_fields(subscription_id: int, **fields) -> dict | None:
    """تحديث بسيط (اسم/ساعة/عدد/ملاحظة/next_send_at/missed) — لا حالة."""
    if not fields:
        return await get_subscription(subscription_id)
    sets, args = [], [subscription_id]
    for k, v in fields.items():
        args.append(_time_value(v) if k == "send_time" else v)
        cast = "::time" if k == "send_time" else ""
        sets.append(f"{k} = ${len(args)}{cast}")
    sets.append("updated_at = now()")
    row = await db.fetchrow(
        f"UPDATE scheduled_subscriptions SET {', '.join(sets)} WHERE id = $1 RETURNING *", *args)
    return _row(row)


async def set_status(subscription_id: int, status: str, expect: tuple | None = None) -> dict | None:
    """انتقال حالة **مشروط** — لا يقلب refunded/cancelled/completed أبداً."""
    if expect is None:
        expect = ACTIVE_STATES + FINAL_STATES
    row = await db.fetchrow(
        "UPDATE scheduled_subscriptions SET status=$2, updated_at=now() "
        "WHERE id=$1 AND status = ANY($3::text[]) RETURNING *",
        subscription_id, status, list(expect))
    return _row(row)


async def extend_totals(subscription_id: int, add_count: int, add_price) -> dict | None:
    row = await db.fetchrow(
        "UPDATE scheduled_subscriptions SET total_items = total_items + $2, "
        "price_usd = price_usd + $3, updated_at = now() "
        "WHERE id = $1 AND status = ANY($4::text[]) RETURNING *",
        subscription_id, int(add_count), add_price, list(ACTIVE_STATES))
    return _row(row)


async def orders_for_sub(subscription_id: int) -> list[dict]:
    return [dict(r) for r in await db.fetch(
        "SELECT * FROM orders WHERE scheduled_subscription_id=$1 ORDER BY id", subscription_id)]


# ───────────────── الأزواج (الطابور) ─────────────────

async def items(subscription_id: int) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM scheduled_subscription_items WHERE subscription_id = $1 ORDER BY seq", subscription_id)
    return [dict(r) for r in rows]


async def get_item(subscription_id: int, seq: int) -> dict | None:
    return _row(await db.fetchrow(
        "SELECT * FROM scheduled_subscription_items WHERE subscription_id = $1 AND seq = $2",
        subscription_id, int(seq)))


async def next_seq(subscription_id: int) -> int:
    return int(await db.fetchval(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM scheduled_subscription_items WHERE subscription_id=$1",
        subscription_id) or 1)


async def add_pair(subscription_id: int, file_kind: str, file_id: str, copy_text: str = "") -> dict:
    """زوج جديد في مؤخرة الطابور — seq تلقائي FIFO."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            seq = int(await c.fetchval(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM scheduled_subscription_items WHERE subscription_id=$1",
                subscription_id) or 1)
            row = await c.fetchrow(
                """
                INSERT INTO scheduled_subscription_items (subscription_id, seq, file_kind, file_id, copy_text)
                VALUES ($1,$2,$3,$4,$5) RETURNING *
                """,
                subscription_id, seq, file_kind, file_id, (copy_text or "")[:1024])
    return dict(row)


async def put_item(subscription_id: int, seq: int, file_kind: str, file_id: str, copy_text: str = "") -> dict | None:
    """حفظ زوج بمسلسل صريح (upsert) — من لوحة التحكم. لا يمس الأزواج المُسلَّمة."""
    row = await db.fetchrow(
        """
        INSERT INTO scheduled_subscription_items (subscription_id, seq, file_kind, file_id, copy_text)
        VALUES ($1,$2,$3,$4,$5)
        ON CONFLICT (subscription_id, seq) DO UPDATE
            SET file_kind=EXCLUDED.file_kind, file_id=EXCLUDED.file_id,
                copy_text=EXCLUDED.copy_text, updated_at=now()
            WHERE scheduled_subscription_items.status <> 'sent'
        RETURNING *
        """,
        subscription_id, int(seq), file_kind, file_id, (copy_text or "")[:1024])
    return _row(row)


async def set_pair_media(subscription_id: int, seq: int, file_kind: str, file_id: str) -> dict | None:
    row = await db.fetchrow(
        "UPDATE scheduled_subscription_items SET file_kind=$3, file_id=$4, updated_at=now() "
        "WHERE subscription_id=$1 AND seq=$2 AND status <> 'sent' RETURNING *",
        subscription_id, int(seq), file_kind, file_id)
    return _row(row)


async def set_sent_message(item_id: int, message_id: int | None) -> None:
    """يخزّن معرّف رسالة التسليم في الوجه (للحذف لاحقاً) أو يمسحه بـNone."""
    await db.execute(
        "UPDATE scheduled_subscription_items SET sent_message_id=$2, updated_at=now() WHERE id=$1",
        item_id, int(message_id) if message_id is not None else None)


async def set_pair_text(subscription_id: int, seq: int, copy_text: str) -> dict | None:
    row = await db.fetchrow(
        "UPDATE scheduled_subscription_items SET copy_text=$3, updated_at=now() "
        "WHERE subscription_id=$1 AND seq=$2 AND status <> 'sent' RETURNING *",
        subscription_id, int(seq), (copy_text or "")[:1024])
    return _row(row)


async def delete_pair(subscription_id: int, seq: int) -> bool:
    res = await db.execute(
        "DELETE FROM scheduled_subscription_items WHERE subscription_id=$1 AND seq=$2 AND status <> 'sent'",
        subscription_id, int(seq))
    return res.endswith("1")


async def claim_next_pair(subscription_id: int) -> dict | None:
    """يحجز زوجاً واحداً (pending → sending) ذرّياً — لا يُرسل نفس الزوج مرتين ولو جاء طلبان معاً."""
    row = await db.fetchrow(
        """
        UPDATE scheduled_subscription_items SET status='sending', attempts=attempts+1, updated_at=now()
        WHERE id = (
            SELECT id FROM scheduled_subscription_items
            WHERE subscription_id=$1
              AND (status='pending' OR (status='sending' AND updated_at < now() - interval '15 minutes'))
            ORDER BY seq LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        subscription_id)
    out = _row(row)
    if out:
        out["attempts"] = int(out.get("attempts") or 0)
    return out


async def due_subscription(gap_minutes: int = 20) -> dict | None:
    """اشتراك حان وقته فيه زوج جاهز — للحلقة الخلفية."""
    row = await db.fetchrow(
        """
        SELECT s.* FROM scheduled_subscriptions s
        WHERE s.status = 'scheduled'
          AND s.next_send_at IS NOT NULL AND s.next_send_at <= now()
          AND EXISTS (SELECT 1 FROM scheduled_subscription_items i
                        WHERE i.subscription_id = s.id
                          AND (i.status='pending' OR (i.status='sending' AND i.updated_at < now() - interval '15 minutes')))
          AND NOT EXISTS (SELECT 1 FROM scheduled_subscription_items r
                        WHERE r.subscription_id = s.id AND r.status='sent'
                          AND r.sent_at > now() - make_interval(mins => $1))
        ORDER BY s.next_send_at, s.id
        FOR UPDATE OF s SKIP LOCKED
        LIMIT 1
        """,
        max(1, gap_minutes))
    return _row(row)


async def mark_sent(item_id: int, subscription_id: int, *, missed_chain: bool, next_send_at: datetime) -> dict | None:
    """تسليم ناجح — تحديث مشروط: لا يمسّ اشتراكاً مسترداً/ملغياً/مكملاً."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                "UPDATE scheduled_subscription_items SET status='sent', sent_at=now(), last_error=NULL, updated_at=now() "
                "WHERE id=$1 AND status IN ('sending','pending')",
                item_id)
            row = await c.fetchrow(
                """
                UPDATE scheduled_subscriptions s SET
                    sent_count = (SELECT count(*) FROM scheduled_subscription_items
                                   WHERE subscription_id = s.id AND status='sent'),
                    last_sent_at = now(),
                    missed_slot = $2,
                    next_send_at = $3,
                    last_error = NULL,
                    status = CASE WHEN (SELECT count(*) FROM scheduled_subscription_items
                                         WHERE subscription_id = s.id AND status='sent') >= s.total_items
                             THEN 'completed' ELSE 'scheduled' END,
                    completed_at = CASE WHEN (SELECT count(*) FROM scheduled_subscription_items
                                               WHERE subscription_id = s.id AND status='sent') >= s.total_items
                             THEN now() ELSE NULL END,
                    updated_at = now()
                WHERE s.id = $1 AND s.status IN ('scheduled','paused')
                RETURNING *
                """,
                subscription_id, bool(missed_chain), next_send_at)
    return _row(row)


async def mark_failed(item_id: int, subscription_id: int, error: str, retry: bool) -> dict | None:
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                "UPDATE scheduled_subscription_items SET status=$2, last_error=$3, updated_at=now() WHERE id=$1",
                item_id, "pending" if retry else "failed", (error or "")[:500])
            row = await c.fetchrow(
                """
                UPDATE scheduled_subscriptions SET
                    status = CASE WHEN $2 THEN status ELSE 'paused' END,
                    last_error = $3, updated_at = now()
                WHERE id = $1 AND status IN ('scheduled','paused')
                RETURNING *
                """,
                subscription_id, bool(retry), (error or "")[:500])
    return _row(row)


async def counts() -> dict:
    row = await db.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM scheduled_subscriptions WHERE status='scheduled') AS active,
          (SELECT count(*) FROM scheduled_subscriptions WHERE status='paused') AS paused,
          (SELECT count(*) FROM scheduled_subscriptions WHERE status='completed') AS done,
          (SELECT count(*) FROM scheduled_subscription_items i JOIN scheduled_subscriptions s ON s.id=i.subscription_id
             WHERE i.status='sent' AND i.sent_at >= date_trunc('day', now())) AS sent_today,
          (SELECT count(*) FROM scheduled_subscriptions s
             WHERE s.status='scheduled'
               AND (SELECT count(*) FROM scheduled_subscription_items i
                      WHERE i.subscription_id=s.id AND i.status IN ('pending','sending')) < 3) AS low_buffer
        """)
    return dict(row) or {}
