"""إشعارات الطلبات: بطاقة الأدمن (وتحديثها بعد كل تغيير) + رسائل الحالة للعميل."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db.repo import orders as repo, users as users_repo
from app.services import nour, orders as orders_svc, pricing as P, targeting as TG
from app.services.pricing import fmt

log = logging.getLogger("order_notify")
TZ = ZoneInfo(settings.tz)


def esc(s: str | None) -> str:
    return html.escape(str(s) if s is not None else "", quote=False)


def _when(dt: datetime | None) -> str:
    return dt.astimezone(TZ).strftime("%d/%m %H:%M") if dt else "—"


def pkg_label(spec: dict) -> str:
    code = spec.get("pkg")
    if code in P.META_BY_CODE:
        p = P.META_BY_CODE[code]
        return f"{p.emoji} {p.title}"
    if code == "bundle" or spec.get("bundle"):
        return f"📦 {P.BUNDLE_STORE_LAUNCH['title']}"
    return "🛠️ مخصص"


def geo_label(spec: dict) -> str:
    return f"{TG.country_label(spec.get('country', ''))} — {TG.provinces_label(spec.get('country', ''), spec.get('provinces'))}"


async def admin_card_text(order: dict, media_count: int) -> str:
    spec = order["spec"]
    charged = f" · خصم فعلي <b>{fmt(order['charged_usd'])}</b>" if order.get("charged_usd") is not None else ""
    margin = P.money(order["price_usd"] - (order.get("charged_usd") if order.get("charged_usd") is not None else order["cost_usd"]))
    addons = spec.get("addons") or []
    addon_txt = ""
    if addons:
        addon_txt = " · ✍️ " + " + ".join(P.ADDONS[a]["title"] for a in addons if a in P.ADDONS)
    uname = f"@{order['user_username']}" if order.get("user_username") else ""
    tg = f"@{spec['tg_username']}" if spec.get("tg_username") else "⚠️ بلا معرّف (يُرسل معرّفك الاحتياطي)"
    note = f"\n📌 <i>{esc(order['note'])}</i>" if order.get("note") else ""
    return T.ADMIN_ORDER_CARD.format(
        icon=orders_svc.STATUS_ICON.get(order["status"], "•"), id=order["id"], status=orders_svc.STATUS_NAME.get(order["status"], order["status"]),
        dry=" 🧪" if nour.is_dry_run() else "", name=esc(order.get("user_name")), username=esc(uname), uid=order["user_id"],
        pkg=pkg_label(spec), platform=TG.PLATFORM_NAME.get(spec.get("platform"), spec.get("platform")),
        goal=TG.GOAL_NAME.get(spec.get("goal"), spec.get("goal")), geo=geo_label(spec),
        gender=TG.GENDER_NAME.get(spec.get("gender", "all"), ""), age=TG.age_label(int(spec.get("age_min", 18)), int(spec.get("age_max", 65))),
        daily=fmt(spec["daily"]), days=P.days_word(spec["days"]), budget=fmt(P.money(P.D(str(spec["daily"])) * int(spec["days"]))),
        price=fmt(order["price_usd"]), cost=fmt(order["cost_usd"]), charged=charged, margin=fmt(margin),
        link=esc(spec.get("link")) or "—", desc=esc(spec.get("desc")) or "—",
        media=f"{media_count} 📎" if media_count else "لا شيء", addons=addon_txt, wa=esc(spec.get("whatsapp")), tg=tg,
        nour_id=order.get("nour_id") or "—", nour_status=order.get("nour_status") or "—", created=_when(order.get("paid_at") or order.get("created_at")),
        note=note,
    )


async def notify_admins_new_order(bot: Bot, order_id: int) -> None:
    order = await repo.get(order_id)
    if not order:
        return
    media_count = len(await repo.media(order_id))
    text = await admin_card_text(order, media_count)
    kb = K.admin_order_card(order, nour.is_dry_run(), media_count)
    msg_ids: list[list[int]] = []
    for admin_id in settings.admin_ids:
        try:
            m = await bot.send_message(admin_id, text, reply_markup=kb)
            msg_ids.append([admin_id, m.message_id])
        except Exception as e:  # noqa: BLE001
            log.warning("cannot notify admin %s about order %s: %s", admin_id, order_id, e)
    if msg_ids:
        await repo.set_messages(order_id, admin_msg_ids=msg_ids)
    if order["status"] == "paid" and order.get("note"):
        await notify_admins_text(bot, T.ADMIN_ORDER_ALERT_STUCK.format(id=order_id, note=esc(order["note"])))
    elif (order.get("note") or "").startswith("⚠️ فرق"):
        await notify_admins_text(bot, T.ADMIN_ORDER_ALERT_CHARGE.format(id=order_id, note=esc(order["note"])))


async def refresh_admin_cards(bot: Bot, order_id: int) -> None:
    """بعد أي تغيير: تحديث بطاقات كل الأدمن (النص + الأزرار المناسبة للحالة الجديدة)."""
    order = await repo.get(order_id)
    if not order:
        return
    media_count = len(await repo.media(order_id))
    text = await admin_card_text(order, media_count)
    kb = K.admin_order_card(order, nour.is_dry_run(), media_count)
    for pair in order.get("admin_msg_ids") or []:
        try:
            chat_id, message_id = pair
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=kb)
        except Exception as e:  # noqa: BLE001 — لم يتغير / قديمة
            log.debug("refresh order card failed %s: %s", pair, e)


async def notify_admins_text(bot: Bot, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            log.info("cannot notify admin %s: %s", admin_id, e)


async def push_user_status(bot: Bot, order: dict, reason: str | None = None) -> None:
    """رسالة للعميل عند تغيّر الحالة (إن كان لها قالب)."""
    tpl = T.ORDER_STATUS_PUSH.get(order["status"])
    if not tpl:
        return
    spec = order["spec"]
    balance = await users_repo.get_balance(order["user_id"])
    text = tpl.format(id=order["id"], wa=spec.get("whatsapp", ""), platform=TG.PLATFORM_NAME.get(spec.get("platform"), ""),
                      days=P.days_word(spec.get("days") or 0), price=fmt(order["price_usd"]), balance=fmt(balance), reason=esc(reason or order.get("note")))
    try:
        await bot.send_message(order["user_id"], text, reply_markup=K.order_view({**order, "media_count": 0}))
    except Exception as e:  # noqa: BLE001 — حظر البوت
        log.warning("cannot push status to user %s: %s", order["user_id"], e)
