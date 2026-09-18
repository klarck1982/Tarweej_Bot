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
from app.services import channels, nour, orders as orders_svc, pricing as P, targeting as TG
from app.services.pricing import fmt

log = logging.getLogger("order_notify")
TZ = ZoneInfo(settings.tz)


def esc(s: str | None) -> str:
    return html.escape(str(s) if s is not None else "", quote=False)


def _when(dt: datetime | None) -> str:
    return dt.astimezone(TZ).strftime("%d/%m %H:%M") if dt else "—"


def pkg_label(spec: dict) -> str:
    if spec.get("kind") == "tg_ads":
        return f"📣 تيليغرام {fmt(spec.get('budget', 0))}"
    if spec.get("kind") == "tg_post":
        return f"📝 {str(spec.get('channel_title') or 'قناة')[:14]}"
    code = spec.get("pkg")
    if code in P.META_BY_CODE:
        p = P.META_BY_CODE[code]
        return f"{p.emoji} {p.title}"
    if code == "bundle" or spec.get("bundle"):
        return f"📦 {P.BUNDLE_STORE_LAUNCH['title']}"
    return "🛠️ مخصص"


def geo_label(spec: dict) -> str:
    return f"{TG.country_label(spec.get('country', ''))} — {TG.provinces_label(spec.get('country', ''), spec.get('provinces'))}"


def tga_results_line(order: dict) -> str:
    r = order.get("results") or {}
    if not r:
        return ""
    return T.TGA_RESULTS_LINE.format(views=f"{int(r.get('views', 0)):,}", clicks=f"{int(r.get('clicks', 0)):,}")


async def admin_tga_card_text(order: dict) -> str:
    spec = order["spec"]
    uname = f"@{order['user_username']}" if order.get("user_username") else ""
    margin = P.money(order["price_usd"] - order["cost_usd"])
    if "copy" in (spec.get("addons") or []):
        addons = "\n✍️ نص إعلاني (+5$) — <b>كتبه الفريق ✅</b>" if spec.get("text") else "\n✍️ <b>مطلوب: كتابة النص</b> (+5$) — اكتبه قبل إنشاء الإعلان"
    else:
        addons = ""
    revision = f"\n✏️ طلب تعديل: <i>{esc(order['revision_note'])}</i>" if order.get("revision_note") else ""
    prev = f"\n<s>{esc(spec['text_prev'])}</s>" if spec.get("text_prev") else ""
    note = f"\n📌 <i>{esc(order['note'])}</i>" if order.get("note") else ""
    text = spec.get("text") or "— (لم يُكتب بعد — اضغط «أدخل النص الذي كتبته»)"
    n_label = f"{len(spec['text'])} حرفاً" if spec.get("text") else "مطلوب"
    return T.ADMIN_TGA_CARD.format(
        icon=orders_svc.STATUS_ICON.get(order["status"], "•"), id=order["id"], status=orders_svc.status_name(order),
        name=esc(order.get("user_name")), username=esc(uname), uid=order["user_id"],
        budget=fmt(spec["budget"]), price=fmt(order["price_usd"]), margin=fmt(margin),
        targeting=esc(TG.tga_targeting_label(spec)), n=n_label, text=esc(text) + prev,
        link=esc(spec.get("link")), addons=addons, revision=revision, results=tga_results_line(order),
        created=_when(order.get("paid_at") or order.get("created_at")), note=note,
    )


def tgp_views_line(order: dict) -> str:
    r = order.get("results") or {}
    if r.get("views") is None:
        return ""
    return T.TGP_VIEWS_LINE.format(views=f"{int(r['views']):,}")


def _tgp_ends(order: dict) -> str:
    return _when(order.get("ends_at"))


async def admin_tgp_card_text(order: dict, media_count: int) -> str:
    from app.services import partner_posts as PP
    spec = order["spec"]
    uname = f"@{order['user_username']}" if order.get("user_username") else ""
    margin = P.money(order["price_usd"] - order["cost_usd"])
    if spec.get("text"):
        content = f"نص {len(spec['text'])} حرفاً" + (f" + {media_count} 📎 (أعلاه)" if media_count else "")
        text = f"\n<code>{esc(spec['text'])}</code>"
    else:
        content = (f"{media_count} 📎 (أعلاه)" if media_count else "—")
        text = ""
    if "copy" in (spec.get("addons") or []):
        addons = "\n✍️ كتابة النص (+5$) — <b>كتبه الفريق ✅</b>" if spec.get("text_by_team") else "\n✍️ <b>مطلوب: كتابة النص</b> (+5$) — اكتبه قبل النشر"
    else:
        addons = ""
    owner = None
    try:
        from app.db.repo import partner_channels as PC
        ch = await PC.get(int(spec.get("channel_id") or 0))
        owner = (ch or {}).get("owner_contact") or None
    except Exception:  # noqa: BLE001
        owner = None
    when_ok = f"\n📅 <b>الموعد المؤكَّد: {_when(order['scheduled_at'])}</b>" if order.get("scheduled_at") else ""
    posted = ""
    if order.get("post_url"):
        posted = f"\n🔗 {esc(order['post_url'])} — نُشر {_when(order.get('started_at'))}"
        if order.get("ends_at"):
            posted += f" · ينتهي {_tgp_ends(order)}"
    note = f"\n📌 <i>{esc(order['note'])}</i>" if order.get("note") else ""
    return T.ADMIN_TGP_CARD.format(
        icon=orders_svc.status_icon(order), id=order["id"], status=orders_svc.status_name(order),
        name=esc(order.get("user_name")), username=esc(uname), uid=order["user_id"],
        title=esc(spec.get("channel_title")), url=esc(spec.get("channel_url")), owner=esc(owner or "—"),
        format=PP.fmt_label(spec.get("format", "24h")),
        when_req=esc(spec.get("when")) if spec.get("when") else "⚡ أقرب وقت", when_ok=when_ok, posted=posted,
        content=content, text=text, addons=addons, price=fmt(order["price_usd"]), cost=fmt(order["cost_usd"]), margin=fmt(margin),
        views=tgp_views_line(order), created=_when(order.get("paid_at") or order.get("created_at")), note=note,
    )


def admin_card_kb(order: dict, media_count: int, in_channel: bool):
    if order.get("kind") == "tg_ads":
        return K.admin_tga_card(order, in_channel=in_channel)
    if order.get("kind") == "tg_post":
        return K.admin_tgp_card(order, media_count, in_channel=in_channel)
    return K.admin_order_card(order, nour.is_dry_run(), media_count, in_channel=in_channel)


async def admin_card_text(order: dict, media_count: int) -> str:
    if order.get("kind") == "tg_ads":
        return await admin_tga_card_text(order)
    if order.get("kind") == "tg_post":
        return await admin_tgp_card_text(order, media_count)
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
    targets = await channels.chat_ids_for("orders")
    to_channel = bool(targets) and channels.is_channel_chat(targets[0])
    kb = admin_card_kb(order, media_count, to_channel)
    if order.get("kind") == "tg_post" and media_count:
        await _forward_media(bot, order_id, targets)
    msg_ids = await channels.send(bot, "orders", text, kb)
    if msg_ids:
        await repo.set_messages(order_id, admin_msg_ids=msg_ids)
    if order["status"] == "paid" and order.get("note"):
        if "رصيد نور" in order["note"]:
            await notify_admins_text(bot, T.ADMIN_ORDER_ALERT_NOUR_BALANCE.format(id=order_id, note=esc(order["note"])))
            # العميل: طمأنة بأن الطلب مقبول وقد يتأخر قليلاً (بلا ذكر رصيدنا)
            try:
                await bot.send_message(order["user_id"], T.META_DELAY_NOTICE.format(id=order_id))
            except Exception as e:  # noqa: BLE001
                log.debug("delay notice failed for ORD-%s: %s", order_id, e)
        else:
            await notify_admins_text(bot, T.ADMIN_ORDER_ALERT_STUCK.format(id=order_id, note=esc(order["note"])))
    elif (order.get("note") or "").startswith("⚠️ فرق"):
        await notify_admins_text(bot, T.ADMIN_ORDER_ALERT_CHARGE.format(id=order_id, note=esc(order["note"])))


async def _forward_media(bot: Bot, order_id: int, targets: list[int]) -> None:
    """ملفات العميل تُرسل قبل البطاقة (للنشر كما هي) — إلى الوجهة نفسها."""
    from aiogram.types import InputMediaPhoto, InputMediaVideo
    items = await repo.media(order_id)
    for chat_id in targets:
        try:
            group = []
            for it in items:
                if it["kind"] == "photo":
                    group.append(InputMediaPhoto(media=it["file_id"]))
                elif it["kind"] == "video":
                    group.append(InputMediaVideo(media=it["file_id"]))
                else:
                    await bot.send_document(chat_id, it["file_id"], caption=f"ملف #ORD-{order_id}")
            if len(group) == 1:
                m = group[0]
                if isinstance(m, InputMediaPhoto):
                    await bot.send_photo(chat_id, m.media, caption=f"📎 محتوى #ORD-{order_id}")
                else:
                    await bot.send_video(chat_id, m.media, caption=f"📎 محتوى #ORD-{order_id}")
            elif group:
                await bot.send_media_group(chat_id, group)
        except Exception as e:  # noqa: BLE001
            log.warning("forward media ORD-%s to %s failed: %s", order_id, chat_id, e)


async def refresh_admin_cards(bot: Bot, order_id: int) -> None:
    """بعد أي تغيير: تحديث بطاقات كل الأدمن (النص + الأزرار المناسبة للحالة الجديدة)."""
    order = await repo.get(order_id)
    if not order:
        return
    media_count = len(await repo.media(order_id))
    text = await admin_card_text(order, media_count)
    for pair in order.get("admin_msg_ids") or []:
        try:
            chat_id, message_id = pair
            kb = admin_card_kb(order, media_count, channels.is_channel_chat(chat_id))
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=kb)
        except Exception as e:  # noqa: BLE001 — لم يتغير / قديمة
            log.debug("refresh order card failed %s: %s", pair, e)


async def notify_admins_text(bot: Bot, text: str) -> None:
    """تنبيه نظام → قناة التنبيهات إن رُبطت، وإلا الأدمن في الخاص."""
    await channels.alert(bot, text)


async def push_user_status(bot: Bot, order: dict, reason: str | None = None) -> None:
    """رسالة للعميل عند تغيّر الحالة (إن كان لها قالب) — قوالب مختلفة لكل نوع طلب."""
    spec = order["spec"]
    st = order["status"]
    balance = await users_repo.get_balance(order["user_id"])
    kb = K.order_view({**order, "media_count": 0})
    if order.get("kind") == "tg_post":
        from app.services import partner_posts as PP
        tpl = T.TGP_STATUS_PUSH.get(st)
        if not tpl:
            return
        kb = K.tgp_order_view({**order, "media_count": 0})
        text = tpl.format(id=order["id"], title=esc(spec.get("channel_title")), when=_when(order.get("scheduled_at")), tz=settings.tz,
                          url=esc(order.get("post_url") or ""), format=PP.fmt_label(spec.get("format", "24h")), ends=_tgp_ends(order),
                          views=tgp_views_line(order), reason=esc(reason or order.get("note") or "—"),
                          price=fmt(order.get("refunded_usd") or order["price_usd"]), balance=fmt(balance))
        try:
            await bot.send_message(order["user_id"], text, reply_markup=kb)
        except Exception as e:  # noqa: BLE001
            log.warning("cannot push status to user %s: %s", order["user_id"], e)
        return
    if order.get("kind") == "tg_ads":
        if st == "needs_revision":
            text = T.TGA_REVISION_PROMPT.format(id=order["id"], reason=esc(order.get("revision_note") or reason or "—"),
                                                text=esc(spec.get("text") or "—"))
            kb = K.tga_revision(order["id"])
        else:
            tpl = T.TGA_STATUS_PUSH.get(st) or (T.ORDER_STATUS_PUSH.get(st) if st in ("refunded", "failed_submit") else None)
            if not tpl:
                return
            from app.db.repo import settings as settings_repo
            hours = str(await settings_repo.get("tg_ads_review_hours", "1 – 24"))
            text = tpl.format(id=order["id"], budget=fmt(spec.get("budget", 0)), price=fmt(order["price_usd"]), balance=fmt(balance),
                              reason=esc(reason or order.get("note") or "—"), hours=hours, results=tga_results_line(order))
    else:
        tpl = T.ORDER_STATUS_PUSH.get(st)
        if not tpl:
            return
        text = tpl.format(id=order["id"], wa=spec.get("whatsapp", ""), platform=TG.PLATFORM_NAME.get(spec.get("platform"), ""),
                          days=P.days_word(spec.get("days") or 0), price=fmt(order["price_usd"]), balance=fmt(balance),
                          reason=esc(reason or order.get("note")))
    try:
        await bot.send_message(order["user_id"], text, reply_markup=kb)
    except Exception as e:  # noqa: BLE001 — حظر البوت
        log.warning("cannot push status to user %s: %s", order["user_id"], e)
