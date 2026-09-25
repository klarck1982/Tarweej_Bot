"""💼 إشعارات سوق القنوات — كل ما يرسله البوت لصاحب القناة والعميل والأدمن (يستدعيه المعالجات والمجدول)."""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot

from app.bot import keyboards as K
from app.bot import mp_keyboards as KB
from app.bot import mp_texts as TX
from app.config import settings
from app.db.repo import orders as repo, partner_channels as PC
from app.services import channels
from app.services import marketplace as MP
from app.services import order_notify as ON
from app.services import partner_posts as PP
from app.services.pricing import fmt

log = logging.getLogger(__name__)
esc = ON.esc


def when(dt: datetime | None) -> str:
    return ON._when(dt)


def prices_line(ch: dict, client: bool = False) -> str:
    parts = []
    for code, price, cost in PP.formats_for(ch):
        parts.append(f"{PP.fmt_label(code)} <b>{fmt(price if client else cost)}</b>")
    return " · ".join(parts) or "—"


async def _send(bot: Bot, chat_id: int, text: str, kb=None) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=kb)
        return True
    except Exception as e:  # noqa: BLE001 — حظر البوت / لم يبدأ محادثة
        log.info("mp notify %s failed: %s", chat_id, e)
        return False


async def owner_note(bot: Bot, order: dict, text: str, kb=None) -> None:
    if order.get("owner_user_id"):
        await _send(bot, int(order["owner_user_id"]), text, kb or KB.order(order))


# ───────────── صاحب القناة ─────────────

def request_text(o: dict) -> str:
    spec = o["spec"] or {}
    media_n = int(spec.get("media_n") or 0)
    return TX.REQ_CARD.format(
        id=o["id"], title=esc(spec.get("channel_title")), format=PP.fmt_label(spec.get("format", "24h")),
        when=esc(spec.get("when")) if spec.get("when") else "⚡ أقرب وقت", chars=len(spec.get("text") or ""),
        media=media_n or "لا يوجد", earn=fmt(o["cost_usd"]), deadline=when(o.get("owner_deadline")),
    )


async def send_owner_request(bot: Bot, o: dict) -> None:
    """معاينة المنشور كما سيظهر (نفس دالة النشر) ثم بطاقة القبول/الاعتذار."""
    owner = int(o["owner_user_id"])
    try:
        await MP._send_post(bot, owner, (o["spec"] or {}).get("text"), await repo.media(o["id"]))
    except Exception as e:  # noqa: BLE001
        log.info("owner preview ORD-%s failed: %s", o["id"], e)
    await _send(bot, owner, TX.REQ_PREVIEW.format(id=o["id"]) + "\n\n" + request_text(o), KB.request(o["id"]))


async def after_customer_paid(bot: Bot, order_id: int) -> None:
    """يُستدعى بعد دفع العميل، وبعد أن يكتب فريقنا النص (إضافة «اكتبولي»)."""
    try:
        o, send = await MP.dispatch(order_id)
    except Exception as e:  # noqa: BLE001
        log.warning("mp dispatch ORD-%s failed: %s", order_id, e)
        return
    if o and send:
        await send_owner_request(bot, o)
    if o and o.get("owner_user_id"):
        await ON.refresh_admin_cards(bot, order_id)


# ───────────── العميل ─────────────

async def customer_push(bot: Bot, o: dict, reason: str | None = None) -> None:
    spec = o["spec"] or {}
    if o.get("owner_user_id") and o["status"] == "active":
        text = TX.PUSH_ACTIVE.format(id=o["id"], title=esc(spec.get("channel_title")), url=esc(o.get("post_url") or ""),
                                     format=PP.fmt_label(spec.get("format", "24h")), ends=when(o.get("ends_at")))
        await _send(bot, o["user_id"], text, K.tgp_order_view({**o, "media_count": 0}))
        return
    if o["status"] == "completed":
        text = TX.PUSH_COMPLETED.format(id=o["id"], title=esc(spec.get("channel_title")))
        await _send(bot, o["user_id"], text, KB.rate(o["id"]))
        return
    await ON.push_user_status(bot, o, reason=reason)


# ───────────── أحداث الطلب ─────────────

async def on_published(bot: Bot, o: dict) -> None:
    spec = o["spec"] or {}
    await customer_push(bot, o)
    await owner_note(bot, o, TX.PUBLISHED_OWNER.format(id=o["id"], title=esc(spec.get("channel_title")), url=esc(o.get("post_url")),
                                                       ends=when(o.get("ends_at")), earn=fmt(o["cost_usd"])))
    await ON.refresh_admin_cards(bot, o["id"])


async def on_refunded(bot: Bot, o: dict, why: str) -> None:
    """why: timeout | early | publish | owner | admin | cancel."""
    spec = o["spec"] or {}
    title = esc(spec.get("channel_title"))
    if why != "cancel":
        await ON.push_user_status(bot, o, reason=o.get("note"))
    notes = {"timeout": TX.NOTE_TIMEOUT, "early": TX.NOTE_EARLY, "publish": TX.NOTE_PUBLISH_REFUND, "cancel": TX.NOTE_CANCELLED,
             "admin": TX.NOTE_ADMIN_REJECT}
    if why in notes:
        await owner_note(bot, o, notes[why].format(id=o["id"], title=title, reason=esc(o.get("note") or "—")))
    if why in ("early", "publish"):
        await ON.notify_admins_text(bot, f"💼 #ORD-{o['id']} ({title}): {esc(o.get('note') or '')}")
    await ON.refresh_admin_cards(bot, o["id"])


async def on_publish_retry(bot: Bot, o: dict) -> None:
    if int(o.get("publish_attempts") or 0) == 1:    # تنبيه واحد فقط لصاحب القناة
        await owner_note(bot, o, TX.NOTE_PUBLISH_FAIL.format(id=o["id"], title=esc((o["spec"] or {}).get("channel_title"))))


async def on_completed(bot: Bot, o: dict) -> None:
    spec = o["spec"] or {}
    await customer_push(bot, o)
    if o.get("owner_user_id"):
        deleted = TX.NOTE_COMPLETED_DEL if o.get("unpublished_at") else (TX.NOTE_COMPLETED_NODEL if o.get("channel_msg_ids") else "")
        release = when(o.get("payout_at")) if o.get("payout_at") else "قريباً"
        await owner_note(bot, o, TX.NOTE_COMPLETED.format(id=o["id"], title=esc(spec.get("channel_title")), deleted=deleted,
                                                          earn=fmt(o["cost_usd"]), release=release), KB.to_earn())
    await ON.refresh_admin_cards(bot, o["id"])


async def on_released(bot: Bot, o: dict) -> None:
    e = await MP.earnings(int(o["owner_user_id"]))
    await owner_note(bot, o, TX.NOTE_RELEASED.format(id=o["id"], earn=fmt(o["cost_usd"]), avail=fmt(e["avail"])), KB.to_earn())


# ───────────── الأدمن ─────────────

def admin_channel_text(ch: dict, owner_name: str = "", full: bool = False) -> str:
    kw = dict(title=esc(ch["title"]), url=esc(ch["url"]), subs=PP.subs_label(ch["subscribers"]), cat=PC.cat_label(ch["category"]),
              prices=prices_line(ch), client=prices_line(ch, client=True), blurb=esc(ch.get("blurb") or "—"),
              owner=esc(owner_name or "—"), owner_id=ch.get("owner_user_id"))
    if not full:
        return TX.ADM_NEW_CHANNEL.format(**kw)
    status = channel_status(ch)
    r = MP.rating_label(ch)
    return TX.ADM_CHANNEL.format(**kw, status=status, done=ch["done_n"], rej=ch["reject_n"], tout=ch["timeout_n"],
                                 early=ch["early_n"], rating=f" · {r}" if r else "")


def channel_status(ch: dict) -> str:
    st = ch["mp_status"]
    if st == "approved" and not ch["enabled"]:
        st = "approved_paused"
    return TX.STATUS.get(st, st).format(note=esc(ch.get("mp_note") or "—"))


async def notify_admins_channel(bot: Bot, ch: dict, owner_name: str) -> None:
    await channels.send(bot, "orders", admin_channel_text(ch, owner_name), KB.adm_channel(ch))


def payout_text(p: dict) -> str:
    status = {"pending": "🕐 بانتظار التحويل", "paid": "✅ حُوّل", "rejected": "❌ مرفوض"}[p["status"]]
    if p.get("note"):
        status += f" — {esc(p['note'])}"
    return TX.ADM_PAYOUT.format(id=p["id"], name=esc(p.get("user_name")), username=f"@{esc(p['user_username'])}" if p.get("user_username") else "",
                                uid=p["user_id"], amount=fmt(p["amount_usd"]), method=MP.PAYOUT_METHODS.get(p["method"], p["method"]),
                                address=esc(p["address"]), status=status)


async def notify_admins_payout(bot: Bot, p: dict) -> None:
    pairs = await channels.send(bot, "orders", payout_text(p), KB.adm_payout(p["id"], True))
    if pairs:
        await MP.set_payout_msgs(p["id"], pairs)


async def refresh_payout_cards(bot: Bot, p: dict) -> None:
    for chat_id, mid in (p.get("admin_msg_ids") or []) if isinstance(p.get("admin_msg_ids"), list) else []:
        try:
            await bot.edit_message_text(payout_text(p), chat_id=chat_id, message_id=mid, reply_markup=KB.adm_payout(p["id"], p["status"] == "pending"))
        except Exception as e:  # noqa: BLE001
            log.debug("refresh payout card failed: %s", e)


def dispute_text(o: dict) -> str:
    spec = o["spec"] or {}
    return TX.ADM_DISPUTE.format(
        id=o["id"], title=esc(spec.get("channel_title")), format=PP.fmt_label(spec.get("format", "24h")), uid=o["user_id"],
        owner=o.get("owner_user_id"), url=esc(o.get("post_url") or "—"), started=when(o.get("started_at")),
        completed=when(o.get("completed_at")), verified=when(o.get("verified_at")),
        unpub="نعم" if o.get("unpublished_at") else "لا", price=fmt(o["price_usd"]), cost=fmt(o["cost_usd"]))


async def notify_admins_dispute(bot: Bot, o: dict) -> None:
    await channels.send(bot, "orders", dispute_text(o), KB.adm_dispute(o["id"]))


def tz() -> str:
    return settings.tz
