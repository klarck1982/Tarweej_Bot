"""🧨 تصفير ما قبل الانطلاق (v0.7.0).

يمسح كل البيانات التشغيلية التجريبية ويعيد العدّادات إلى 1 — ويُبقي الإعدادات:
    يُمسح:  المستخدمون وأرصدتهم، دفتر الحركات، الشحنات، الطلبات وملفاتها، المهام، التذاكر، حالات FSM،
            الأحداث (عدا سجل تغييرات Cpanel)، القنوات الشريكة التجريبية (خيار المستخدم: ops_partner)،
            حالة صحة نور (nour_health) ورصيد المحاكاة.
    يبقى:   الأسعار، طرق الدفع، الخدمات، الإعدادات العامة، قنوات الإدارة، سجل تغييرات Cpanel، الترحيلات.

حماية:
    - ممنوع إن وُجدت حملات حقيقية مفتوحة عند نور (الوضع الحقيقي + طلبات submitted/in_progress/active/paused لها nour_id).
    - يجب كتابة كلمة التأكيد «تصفير» حرفياً.
    - يُنفَّذ في معاملة واحدة: إما كل شيء أو لا شيء.
    - الحسابات الإدارية (ADMIN_IDS) تُعاد إنشاؤها فوراً بلا رصيد حتى تبقى لوحة الإدارة تعمل.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import settings
from app.db import pool as db
from app.db.repo import settings as settings_repo

log = logging.getLogger("launch_reset")

CONFIRM_WORD = "تصفير"

# الجداول التشغيلية بترتيب آمن للمفاتيح الأجنبية (TRUNCATE ... CASCADE يتكفّل بالباقي)
_OPS_TABLES = ("ticket_messages", "tickets", "tasks", "order_media", "orders", "topups", "ledger", "fsm_state", "users")
# مفاتيح settings التشغيلية التي تُحذف (ليست إعدادات)
_OPS_SETTINGS = ("nour_health", "dry_nour_balance_usd")


async def preview() -> dict:
    """الأرقام التي ستُعرض قبل التأكيد + هل التصفير مسموح الآن."""
    counts = {}
    for t in ("users", "ledger", "topups", "orders", "order_media", "tasks", "tickets", "events", "partner_channels"):
        counts[t] = int(await db.fetchval(f"SELECT count(*) FROM {t}") or 0)
    counts["cpanel_changes"] = int(await db.fetchval("SELECT count(*) FROM events WHERE type = 'cpanel_change'") or 0)
    counts["events"] -= counts["cpanel_changes"]
    balances = await db.fetchval("SELECT coalesce(sum(balance_usd),0) FROM users") or 0
    from app.services import nour
    dry = nour.is_dry_run()
    live_open = 0
    if not dry:
        live_open = int(await db.fetchval(
            "SELECT count(*) FROM orders WHERE kind='meta_campaign' AND nour_id IS NOT NULL "
            "AND status IN ('submitted','in_progress','active','paused')") or 0)
    blockers = []
    if live_open:
        blockers.append(f"يوجد {live_open} حملة حقيقية مفتوحة عند Nour Ads — انتظر اكتمالها أو استردّها أولاً.")
    return {"counts": counts, "client_balances": float(balances), "dry": dry, "live_open": live_open,
            "allowed": not blockers, "blockers": blockers, "confirm_word": CONFIRM_WORD,
            "keeps": ["الأسعار", "طرق الدفع", "الخدمات والصيانة", "الإعدادات العامة", "قنوات الإدارة", "سجل تغييرات Cpanel"],
            "wipes": ["المستخدمون وأرصدتهم", "الشحنات", "الطلبات وملفاتها", "المهام والتذاكر", "دفتر الحركات والأحداث", "القنوات الشريكة"]}


async def execute(admin_id: int, confirm: str, wipe_partner: bool = True) -> dict:
    """ينفّذ التصفير. يرمي ValueError برسالة عربية إن لم يُسمح."""
    if (confirm or "").strip() != CONFIRM_WORD:
        raise ValueError(f"اكتب كلمة «{CONFIRM_WORD}» بالضبط للتأكيد")
    pv = await preview()
    if not pv["allowed"]:
        raise ValueError(" ".join(pv["blockers"]))
    before = pv["counts"]
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute("TRUNCATE " + ", ".join(_OPS_TABLES) + " RESTART IDENTITY CASCADE")
            await c.execute("DELETE FROM events WHERE type <> 'cpanel_change'")
            if wipe_partner:
                await c.execute("TRUNCATE partner_channels RESTART IDENTITY CASCADE")
            await c.execute("DELETE FROM settings WHERE key = ANY($1::text[])", list(_OPS_SETTINGS))
            # الأدمن يعود مستخدماً فوراً (بلا رصيد) حتى تعمل لوحة الإدارة والإشعارات
            for aid in settings.admin_ids:
                await c.execute(
                    "INSERT INTO users (tg_id, name, accepted_terms_at, created_at) VALUES ($1, $2, now(), now()) ON CONFLICT (tg_id) DO NOTHING",
                    int(aid), "admin")
            await c.execute(
                "INSERT INTO events (user_id, type, payload) VALUES ($1, 'cpanel_change', $2::jsonb)",
                int(admin_id),
                '{"section":"danger","key":"launch_reset","before":"%s","after":"0 — انطلاقة"}' % _summary(before))
    settings_repo.invalidate()
    log.warning("LAUNCH RESET by %s — wiped %s", admin_id, _summary(before))
    return {"ok": True, "before": before, "wipe_partner": wipe_partner, "at": datetime.now(timezone.utc).isoformat()}


def _summary(c: dict) -> str:
    return f"{c.get('users', 0)} مستخدم / {c.get('orders', 0)} طلب / {c.get('topups', 0)} شحنة / {c.get('partner_channels', 0)} قناة"
