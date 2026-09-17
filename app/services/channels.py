"""قنوات الإدارة: توزيع بطاقات الأدمن على قنوات خاصة بدل خاصّ الأدمن.

ثلاثة أنواع (كلها اختيارية — ما لم يُربط يصل إلى خاصّ الأدمن كالمعتاد):
    topups → بطاقات الشحن #TOP      orders → بطاقات الطلبات #ORD      alerts → تنبيهات النظام

التخزين في جدول settings تحت المفتاح "channels":
    {"topups": {"id": -100123, "title": "إيداعات ترويج"}, "orders": {...}, "alerts": {...}}

القاعدة الذهبية: الأزرار داخل القناة تعمل، لكن أي شيء يحتاج كتابة (سبب رفض، مبلغ، رسالة) يُكمَل في خاصّ الأدمن
(انظر app/bot/handlers/admin/_common.py) — والبطاقة في القناة تتحدّث تلقائياً بالنتيجة.
"""

from __future__ import annotations

import logging

from aiogram import Bot

from app.config import settings
from app.db.repo import settings as settings_repo

log = logging.getLogger("channels")

KINDS: dict[str, tuple[str, str]] = {
    "topups": ("📥", "قناة الإيداعات"),
    "orders": ("📦", "قناة الطلبات"),
    "alerts": ("🔔", "قناة التنبيهات"),
}
KEY = "channels"


def label(kind: str) -> str:
    e, name = KINDS[kind]
    return f"{e} {name}"


async def all_cfg() -> dict:
    val = await settings_repo.get(KEY, {}) or {}
    return dict(val)


async def get(kind: str) -> dict | None:
    cfg = await all_cfg()
    ch = cfg.get(kind)
    return ch if ch and ch.get("id") else None


async def set_(kind: str, chat_id: int, title: str) -> None:
    cfg = await all_cfg()
    cfg[kind] = {"id": int(chat_id), "title": (title or str(chat_id))[:64]}
    await settings_repo.set_(KEY, cfg)


async def unset(kind: str) -> None:
    cfg = await all_cfg()
    if kind in cfg:
        cfg.pop(kind)
        await settings_repo.set_(KEY, cfg)


async def kinds_using(chat_id: int) -> list[str]:
    cfg = await all_cfg()
    return [k for k, v in cfg.items() if v and int(v.get("id", 0)) == int(chat_id)]


async def chat_ids_for(kind: str) -> list[int]:
    """أين تُرسل بطاقات هذا النوع الآن؟ القناة إن وُجدت، وإلا كل الأدمن في الخاص.

    لو تعطلت قاعدة البيانات نفسها (وهذا سبب محتمل للتنبيه) نرجع للأدمن مباشرة."""
    try:
        ch = await get(kind)
    except Exception as e:  # noqa: BLE001
        log.warning("channels lookup failed (%s) — falling back to admins", e)
        ch = None
    return [int(ch["id"])] if ch else list(settings.admin_ids)


def is_channel_chat(chat_id: int) -> bool:
    """معرّفات القنوات والمجموعات سالبة — معرّفات المستخدمين موجبة."""
    return int(chat_id) < 0


async def send(bot: Bot, kind: str, text: str, kb=None, photo: str | None = None) -> list[list[int]]:
    """يرسل نصاً (أو صورة مع تعليق) إلى وجهة النوع. يعيد أزواج [chat_id, message_id] لتحديثها لاحقاً.

    إن فشل الإرسال إلى القناة (حُذفت، أو فقد البوت الصلاحية) نرجع تلقائياً إلى الأدمن في الخاص حتى لا تضيع بطاقة.
    """
    targets = await chat_ids_for(kind)
    pairs: list[list[int]] = []
    for chat_id in targets:
        try:
            if photo:
                m = await bot.send_photo(chat_id, photo, caption=text, reply_markup=kb)
            else:
                m = await bot.send_message(chat_id, text, reply_markup=kb)
            pairs.append([chat_id, m.message_id])
        except Exception as e:  # noqa: BLE001
            log.warning("cannot send %s card to %s: %s", kind, chat_id, e)
    if not pairs and targets and is_channel_chat(targets[0]):
        # القناة لم تستقبل — احتياط: الأدمن في الخاص
        for admin_id in settings.admin_ids:
            try:
                if photo:
                    m = await bot.send_photo(admin_id, photo, caption=text, reply_markup=kb)
                else:
                    m = await bot.send_message(admin_id, text, reply_markup=kb)
                pairs.append([admin_id, m.message_id])
            except Exception as e:  # noqa: BLE001
                log.warning("cannot send %s card to admin %s: %s", kind, admin_id, e)
    return pairs


async def alert(bot: Bot, text: str) -> None:
    """تنبيه نظام: قناة التنبيهات إن وُجدت، وإلا الأدمن."""
    await send(bot, "alerts", text)
