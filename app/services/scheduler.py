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
from app.services.pricing import fmt

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


async def _partner_posts(bot: Bot) -> None:
    """📝 القنوات الشريكة: إنهاء تلقائي بعد 24/48 ساعة من النشر + تذكير الأدمن قبل الموعد بساعة."""
    from app.services import partner_posts as PP
    try:
        for o in await PP.finish_due():
            await ON.push_user_status(bot, o)
            await ON.refresh_admin_cards(bot, o["id"])
            await ON.notify_admins_text(bot, T_auto_done(o))
        for o in await PP.reminders_due(60):
            mins = max(1, int((o["scheduled_at"] - datetime.now(o["scheduled_at"].tzinfo)).total_seconds() // 60))
            from app.bot import texts as T
            await ON.notify_admins_text(bot, T.ADMIN_TGP_REMINDER.format(id=o["id"], title=ON.esc(o["spec"].get("channel_title")),
                                                                       when=ON._when(o["scheduled_at"]), mins=mins))
    except Exception as e:  # noqa: BLE001
        log.warning("partner posts job failed: %s", e)


def T_auto_done(o: dict) -> str:
    from app.bot import texts as T
    return T.ADMIN_TGP_AUTO_DONE.format(id=o["id"], title=ON.esc(o["spec"].get("channel_title")))


async def tick(bot: Bot) -> None:
    """دورة واحدة — تُستدعى من الحلقة، ويمكن استدعاؤها يدوياً في الاختبارات."""
    from app.services import cpanel, pricing
    await pricing.refresh()          # لو تغيّرت الأسعار من Cpanel على نسخة أخرى من العملية
    await cpanel.refresh_runtime()
    await _retry_submissions(bot)
    await _expire_drafts(bot)
    await _sync_open_orders(bot)
    await _partner_posts(bot)
    await _design_tasks(bot)
    await _scheduled_designs(bot)
    await _tickets(bot)
    await _nour_health(bot)


async def _scheduled_designs(bot: Bot) -> None:
    """📅 إرسال التصميم + النص المجدول لكل مشترك، مع حجز يمنع التكرار وإعادة محاولة الفشل."""
    from app.bot import texts as T
    from app.services import scheduled as SD
    from app.db.repo import scheduled as SR
    from app.services import order_notify as ON
    for _ in range(10):
        try:
            item = await SR.due_item()
        except Exception as e:  # noqa: BLE001
            log.warning("scheduled content lookup failed: %s", e)
            return
        if not item:
            return
        try:
            kind = item["file_kind"]
            if kind == "photo":
                await bot.send_photo(item["user_id"], item["file_id"])
            elif kind == "video":
                await bot.send_video(item["user_id"], item["file_id"])
            else:
                await bot.send_document(item["user_id"], item["file_id"])
            copy_text = item.get("copy_text") or ""
            if copy_text:
                await bot.send_message(item["user_id"], T.esc(copy_text))
            sub = await SR.mark_sent(item["id"], item["subscription_id"])
            if sub:
                done = int(sub.get("sent_count") or 0)
                total = int(sub.get("total_items") or item.get("total_items") or 0)
                if sub.get("status") == "completed":
                    message = f"✅ اكتملت باقة التصميم — {done}/{total}\nشكراً لاستخدامك خدمتنا."
                else:
                    message = f"🎨 تم إرسال التصميم {item['seq']} من {total}\nالتصميم التالي حسب الموعد المحدد."
                try:
                    await bot.send_message(item["user_id"], message)
                except Exception:
                    pass
                if sub.get("status") == "completed":
                    await ON.notify_admins_text(bot, f"✅ <b>اكتملت باقة التصميم</b>\nSUB-{sub['id']} · {done}/{total}")
        except Exception as e:  # noqa: BLE001
            attempts = int(item.get("attempts") or 1)
            retry = attempts < 3
            sub = await SR.mark_failed(item["id"], item["subscription_id"], str(e), retry=retry)
            if retry:
                log.warning("scheduled delivery SUB-%s day %s failed (%s/%s): %s", item["subscription_id"], item["seq"], attempts, 3, e)
            else:
                await ON.notify_admins_text(bot, f"⚠️ <b>توقفت جدولة تصميم</b>\nSUB-{item['subscription_id']} · اليوم {item['seq']}\nالسبب: {T.esc(str(e)[:250])}")


async def _tickets(bot: Bot) -> None:
    """v0.8.1: إغلاق التذاكر التي صمت فيها الطرفان 72 ساعة بعد آخر رد للفريق."""
    from app.bot import texts as T
    from app.db.repo import tickets as ticket_repo
    from app.services import ticket_notify as TN
    try:
        closed = await ticket_repo.auto_close(72)
    except Exception as e:  # noqa: BLE001
        log.warning("ticket auto-close failed: %s", e)
        return
    for item in closed:
        try:
            await bot.send_message(item["user_id"], T.TICKET_AUTO_CLOSED.format(id=item["id"]))
        except Exception as e:  # noqa: BLE001
            log.debug("ticket auto-close notice failed %s: %s", item["id"], e)
        await TN.refresh_admin(bot, item["id"])


async def _design_tasks(bot: Bot) -> None:
    """v0.8.0: تنبيهات مهل التصميم (اقترب/متأخر — مرة واحدة لكل) + الاعتماد التلقائي بعد صمت العميل."""
    from app.bot import keyboards as K
    from app.bot import texts as T
    from app.services import design as DS, order_notify as ON
    try:
        soon, late = await DS.due_alerts()
    except Exception as e:  # noqa: BLE001
        log.warning("design due alerts failed: %s", e)
        soon, late = [], []
    for o in soon:
        await ON.notify_admins_text(bot, T.ADMIN_DS_DUE_SOON.format(id=o["id"], title=T.esc(o["spec"].get("title")),
                                                                    left=DS.left_label(o.get("due_at")), due=ON._when(o.get("due_at"))))
    for o in late:
        await ON.notify_admins_text(bot, T.ADMIN_DS_LATE.format(id=o["id"], title=T.esc(o["spec"].get("title")), due=ON._when(o.get("due_at"))))
        await ON.refresh_admin_cards(bot, o["id"])
    try:
        approved = await DS.auto_approve_due()
    except Exception as e:  # noqa: BLE001
        log.warning("design auto-approve failed: %s", e)
        approved = []
    for o in approved:
        hours = DS.approve_hours()
        try:
            await bot.send_message(o["user_id"], T.DS_AUTO_APPROVED.format(id=o["id"], hours=hours),
                                   reply_markup=K.ds_order_view({**o, "media_count": 0}))
        except Exception as e:  # noqa: BLE001
            log.debug("auto-approve notice to %s failed: %s", o["user_id"], e)
        await ON.notify_admins_text(bot, T.ADMIN_DS_AUTO_APPROVED_NOTICE.format(id=o["id"], hours=hours, price=fmt(o["price_usd"])))
        await ON.refresh_admin_cards(bot, o["id"])


async def _nour_health(bot: Bot) -> None:
    """v0.7.0: مراقبة رصيد نور (كل ساعة) + تقرير المطابقة اليومي (09:00)."""
    from app.services import nour_health as NH
    try:
        await NH.watch_balance(bot)
    except Exception as e:  # noqa: BLE001
        log.warning("nour balance watch failed: %s", e)
    try:
        await NH.daily_report(bot)
    except Exception as e:  # noqa: BLE001
        log.warning("nour daily report failed: %s", e)


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
