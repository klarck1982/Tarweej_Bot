"""قوالب إشعارات الأدمن والعميل — كل ما يُرسل خارج سياق الضغطة المباشرة."""

from __future__ import annotations

import html
import json
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db import pool as db
from app.db.repo import settings as settings_repo, topups as topups_repo
from app.services import channels
from app.services.pricing import fmt

log = logging.getLogger("notify")
TZ = ZoneInfo(settings.tz)


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


async def topup_card_text(tid: int) -> tuple[str, dict]:
    row = await topups_repo.get(tid)
    if row is None:
        return "", {}
    from app.services import payments as PM  # استيراد متأخر (payments يستورد settings_repo فقط)
    m = (await PM.get_methods()).get(row["method"], {"title": row["method"]})
    m_title = m.get("title", row["method"])
    approved_count = await db.fetchval(
        "SELECT count(*) FROM topups WHERE user_id = $1 AND status = 'approved'", row["user_id"]
    )
    uname = f"@{row['user_username']}" if row["user_username"] else ""
    text = T.ADMIN_TOPUP_CARD.format(
        id=tid, name=esc(row["user_name"]), username=esc(uname), uid=row["user_id"],
        amount=PM.pay_amount(m, row["amount_usd"]) if m.get("currency") != "SYP" else fmt(row["amount_usd"]),
        method=esc(m_title), local=PM.local_note(m, dict(row)), tx=esc(row["proof_text"]) or "—",
        balance=fmt(row["user_balance"]), approved_count=approved_count,
        when=row["created_at"].astimezone(TZ).strftime("%d/%m %H:%M"),
    )
    if row["status"] != "pending":
        mark = {"approved": "✅", "rejected": "❌", "cancelled": "🚫"}.get(row["status"], "•")
        verdict = {"approved": "معتمد", "rejected": f"مرفوض — {esc(row['reason'])}", "cancelled": "ملغى"}.get(row["status"], row["status"])
        when = (row["decided_at"] or row["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        text += T.ADMIN_TOPUP_DONE.format(mark=mark, verdict=verdict, admin=row["admin_id"] or "النظام", when=when)
    return text, dict(row)


async def notify_admins_topup(bot: Bot, tid: int) -> None:
    """يرسل بطاقة طلب الشحن (مع صورة الإثبات إن وُجدت) لكل أدمن ويحفظ معرّفات الرسائل لتعطيلها لاحقاً."""
    text, row = await topup_card_text(tid)
    if not row:
        return
    remaining = max(0, await topups_repo.count_pending() - 1)
    targets = await channels.chat_ids_for("topups")
    to_channel = bool(targets) and channels.is_channel_chat(targets[0])
    # داخل القناة: بلا أزرار تنقّل («التالي»/«القائمة») — البطاقات كلها موجودة هناك أصلاً
    kb = K.admin_topup_card(tid, has_proof_image=bool(row.get("proof_file_id")),
                            remaining=0 if to_channel else remaining, in_channel=to_channel)
    msg_ids = await channels.send(bot, "topups", text, kb, photo=row.get("proof_file_id"))
    if msg_ids:
        await topups_repo.set_messages(tid, admin_msg_ids=msg_ids)


async def refresh_admin_cards(bot: Bot, tid: int) -> None:
    """بعد القرار: يحدّث بطاقات كل الأدمن (نص محسوم + إزالة الأزرار) حتى لا يعتمد أدمن آخر الطلب نفسه."""
    text, row = await topup_card_text(tid)
    if not row:
        return
    pairs = row.get("admin_msg_ids") or []
    if isinstance(pairs, str):  # asyncpg يعيد JSONB كنص
        pairs = json.loads(pairs)
    for pair in pairs:
        try:
            chat_id, message_id = pair
            if row.get("proof_file_id"):
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text, reply_markup=None)
            else:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception as e:  # noqa: BLE001 — رسالة قديمة أو لم تتغير
            log.debug("refresh card failed %s: %s", pair, e)


async def notify_user_topup_result(bot: Bot, row: dict, new_balance=None, adjusted: bool = False) -> None:
    uid = row["user_id"]
    try:
        if row["status"] == "approved":
            has_draft = bool(await db.fetchval(
                "SELECT 1 FROM orders WHERE user_id = $1 AND status = 'awaiting_payment' LIMIT 1", uid
            ))
            tpl = T.TOPUP_APPROVED_ADJUSTED if adjusted else T.TOPUP_APPROVED
            await bot.send_message(uid, tpl.format(amount=fmt(row["amount_usd"]), balance=fmt(new_balance)),
                                   reply_markup=K.topup_approved(has_draft))
        elif row["status"] == "rejected":
            await bot.send_message(uid, T.TOPUP_REJECTED.format(id=row["id"], reason=esc(row["reason"])),
                                   reply_markup=K.topup_rejected())
        # نزيل أزرار رسالة "بانتظار الاعتماد" القديمة
        if row.get("user_msg_id"):
            try:
                await bot.edit_message_reply_markup(chat_id=uid, message_id=row["user_msg_id"], reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001 — المستخدم حظر البوت مثلاً
        log.warning("cannot notify user %s: %s", uid, e)


async def notify_admins_text(bot: Bot, text: str) -> None:
    """تنبيه نظام → قناة التنبيهات إن رُبطت، وإلا الأدمن في الخاص."""
    await channels.alert(bot, text)
