"""تنسيق وإرسال تذاكر الدعم — لا يضع منطق الحالة داخل handlers."""

from __future__ import annotations

import html
import json
import logging

from aiogram import Bot

from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db.repo import tickets as repo
from app.services import channels
from app.services.pricing import fmt

log = logging.getLogger("ticket_notify")


def esc(value) -> str:
    return html.escape(str(value) if value is not None else "", quote=False)


def status_label(status: str) -> tuple[str, str]:
    return {"open": ("🟡", "مفتوحة"), "answered": ("🔵", "تم الرد"), "closed": ("✅", "مغلقة")}.get(status, ("•", status))


def order_line(ticket: dict) -> str:
    if ticket.get("order_id"):
        st = ticket.get("order_status") or "—"
        return f"📦 بخصوص <b>#ORD-{ticket['order_id']}</b> · حالة الطلب: {esc(st)}\n"
    if ticket.get("topup_id"):
        return f"💳 بخصوص الشحن <b>#TOP-{ticket['topup_id']}</b>\n"
    return "❓ سؤال عام\n"


def message_body(message: dict) -> str:
    parts = []
    if message.get("text"):
        parts.append(esc(message["text"]))
    if message.get("file_id"):
        parts.append(T.TICKET_FILE_LINE.format(kind=esc(message.get("file_kind") or "ملف")))
    return "\n".join(parts) or "(مرفق فقط)"


def history_text(messages: list[dict], limit: int = 8) -> str:
    out = []
    for m in messages[-limit:]:
        who = "الفريق" if m.get("is_admin") else "العميل"
        at = m.get("created_at")
        stamp = at.strftime("%d/%m %H:%M") if hasattr(at, "strftime") else ""
        out.append(f"<b>{who}</b> {stamp}:\n{message_body(m)}")
    return "\n\n".join(out) or "لا رسائل بعد."


def client_text(ticket: dict, messages: list[dict]) -> str:
    icon, status = status_label(ticket.get("status", "open"))
    return T.TICKET_VIEW.format(
        id=ticket["id"], status=f"{icon} {status}", order=order_line(ticket),
        updated=(ticket.get("last_msg_at") or ticket.get("created_at")).strftime("%d/%m %H:%M"),
        messages=history_text(messages),
    )


def admin_text(ticket: dict, messages: list[dict]) -> str:
    icon, status = status_label(ticket.get("status", "open"))
    uname = f"@{ticket['user_username']}" if ticket.get("user_username") else ""
    return T.ADMIN_TICKET_CARD.format(
        icon=icon, id=ticket["id"], status=status, name=esc(ticket.get("user_name")),
        username=esc(uname), uid=ticket["user_id"], order=order_line(ticket),
        balance=fmt(ticket.get("user_balance") or 0),
        updated=(ticket.get("last_msg_at") or ticket.get("created_at")).strftime("%d/%m %H:%M"),
        messages=history_text(messages),
    )


async def notify_admins(bot: Bot, ticket_id: int) -> None:
    ticket = await repo.get(ticket_id)
    if not ticket:
        return
    pairs = ticket.get("admin_msg_ids") or []
    if pairs:
        await refresh_admin(bot, ticket_id)
    else:
        text = admin_text(ticket, await repo.messages(ticket_id))
        pairs = await channels.send(bot, "alerts", text, K.admin_ticket_card(ticket))
        if pairs:
            await repo.set_admin_messages(ticket_id, pairs[-8:])
    # إذا كانت الرسالة تحتوي مرفقاً، نرسل نسخة إلى الوجهة نفسها بعد البطاقة.
    last = await repo.last_message(ticket_id)
    if last and last.get("file_id"):
        for chat_id, _ in pairs:
            await send_file(bot, chat_id, last, prefix=f"📎 مرفق التذكرة #TCK-{ticket_id}")


async def refresh_admin(bot: Bot, ticket_id: int) -> None:
    ticket = await repo.get(ticket_id)
    if not ticket:
        return
    text = admin_text(ticket, await repo.messages(ticket_id))
    pairs = ticket.get("admin_msg_ids") or []
    if isinstance(pairs, str):
        try:
            pairs = json.loads(pairs)
        except json.JSONDecodeError:
            pairs = []
    for chat_id, message_id in pairs:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                        reply_markup=K.admin_ticket_card(ticket))
        except Exception as e:  # noqa: BLE001
            log.debug("ticket card refresh failed %s: %s", (chat_id, message_id), e)


async def send_file(bot: Bot, chat_id: int, message: dict, prefix: str = "") -> None:
    file_id = message.get("file_id")
    if not file_id:
        return
    kind = message.get("file_kind") or "document"
    caption = prefix or None
    try:
        if kind == "photo":
            await bot.send_photo(chat_id, file_id, caption=caption)
        elif kind == "video":
            await bot.send_video(chat_id, file_id, caption=caption)
        else:
            await bot.send_document(chat_id, file_id, caption=caption)
    except Exception as e:  # noqa: BLE001
        log.warning("ticket file send failed to %s: %s", chat_id, e)


async def notify_client(bot: Bot, ticket_id: int, body: str | None = None, message: dict | None = None) -> bool:
    ticket = await repo.get(ticket_id)
    if not ticket:
        return False
    try:
        text = T.TICKET_ADMIN_REPLY.format(id=ticket_id, body=body or "وصلتك رسالة جديدة من الفريق.")
        await bot.send_message(ticket["user_id"], text, reply_markup=K.ticket_view(ticket))
        if message and message.get("file_id"):
            await send_file(bot, ticket["user_id"], message, prefix=f"📎 مرفق من الفريق — #TCK-{ticket_id}")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ticket client notify failed %s: %s", ticket["user_id"], e)
        return False
