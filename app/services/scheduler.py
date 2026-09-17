"""حلقة الخلفية (الخطوة 3): مهام دورية خفيفة داخل نفس العملية — لا تحتاج Worker مدفوعاً على Render.

كل دورة (افتراضياً كل 5 دقائق):
  1. إعادة محاولة الطلبات المدفوعة التي لم تُرسل لنور بعد (status = paid و next_retry_at حان).
  2. إلغاء المسودات المنتهية (awaiting_payment أقدم من order_draft_days).
  3. مزامنة حالات الطلبات المفتوحة مع نور (في وضع DRY RUN لا يحدث شيء — الأدمن يحاكي بالأزرار).

ملاحظة Render المجاني: الخدمة تنام بعد 15 دقيقة بلا طلبات؛ UptimeRobot يوقظها كل 5 دقائق، فتعمل الحلقة عملياً
على مدار الساعة. إذا نامت، تُستكمل المهام عند أول استيقاظ — لا شيء يضيع لأن كل شيء في قاعدة البيانات.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.config import settings
from app.db.repo import orders as repo
from app.services import nour, order_notify as ON, orders as orders_svc

log = logging.getLogger("scheduler")

TICK_SECONDS = 300          # كل 5 دقائق
SYNC_DAY_MINUTES = 15       # نهاراً: مزامنة نور كل 15 دقيقة
SYNC_NIGHT_MINUTES = 30     # ليلاً (00–08 بتوقيت المشروع): كل 30 دقيقة

_last_sync: datetime | None = None


def _is_night() -> bool:
    try:
        hour = datetime.now(ZoneInfo(settings.tz)).hour
    except Exception:  # noqa: BLE001
        hour = datetime.utcnow().hour
    return hour < 8


async def _retry_submissions(bot: Bot) -> None:
    for o in await repo.due_for_retry():
        try:
            updated = await orders_svc.submit(o["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("retry submit ORD-%s failed: %s", o["id"], e)
            continue
        if not updated:
            continue
        if updated["status"] == "submitted":
            log.info("ORD-%s submitted on retry", o["id"])
            await ON.notify_admins_new_order(bot, o["id"])
        elif updated["status"] in ("failed_submit", "refunded"):
            await ON.push_user_status(bot, updated)
            await ON.refresh_admin_cards(bot, o["id"])
            await ON.notify_admins_text(bot, f"⚠️ <b>#ORD-{o['id']}</b> فشل الإرسال نهائياً — {ON.esc(updated.get('note') or '')}")


async def _expire_drafts(bot: Bot) -> None:
    try:
        n = await repo.expire_drafts()
    except Exception as e:  # noqa: BLE001
        log.warning("expire drafts failed: %s", e)
        return
    if n:
        log.info("expired %s draft(s)", n)


async def _sync_open_orders(bot: Bot) -> None:
    """مزامنة الحالات مع نور — فقط في الوضع الحقيقي وحسب الجدول نهار/ليل."""
    global _last_sync
    if nour.is_dry_run():
        return
    interval = SYNC_NIGHT_MINUTES if _is_night() else SYNC_DAY_MINUTES
    now = datetime.utcnow()
    if _last_sync and (now - _last_sync).total_seconds() < interval * 60:
        return
    _last_sync = now
    for o in await repo.list_open(limit=50):
        if not o.get("nour_id") or o["status"] in ("paid", "awaiting_payment"):
            continue
        try:
            updated, changed = await orders_svc.sync_one(o["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("sync ORD-%s failed: %s", o["id"], e)
            continue
        if changed and updated:
            await ON.push_user_status(bot, updated)
            await ON.refresh_admin_cards(bot, o["id"])
        await asyncio.sleep(1.2)  # نور: 60 طلباً/دقيقة كحد أقصى


async def tick(bot: Bot) -> None:
    """دورة واحدة — تُستدعى من الحلقة، ويمكن استدعاؤها يدوياً في الاختبارات."""
    from app.services import cpanel, pricing
    await pricing.refresh()          # لو تغيّرت الأسعار من Cpanel على نسخة أخرى من العملية
    await cpanel.refresh_runtime()
    await _retry_submissions(bot)
    await _expire_drafts(bot)
    await _sync_open_orders(bot)


async def run_forever(bot: Bot) -> None:
    log.info("scheduler started (tick every %ss, nour=%s)", TICK_SECONDS, "DRY" if nour.is_dry_run() else "LIVE")
    await asyncio.sleep(20)  # نترك الإقلاع يكتمل أولاً
    while True:
        try:
            await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — الحلقة لا تموت أبداً
            log.exception("scheduler tick error: %s", e)
        await asyncio.sleep(TICK_SECONDS)


def start(bot: Bot) -> asyncio.Task:
    return asyncio.create_task(run_forever(bot), name="scheduler")
