"""طبقات وسيطة تعمل قبل كل معالج:

1. UserMiddleware  — تسجيل/تحديث المستخدم، حجب الموقوفين، تمرير is_admin للمعالجات.
2. ثبات الأخطاء    — أي استثناء غير متوقع يُسجَّل ويُبلَّغ الأدمن، ولا يسقط البوت.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from app.bot import texts as T
from app.config import settings
from app.db.repo import users as users_repo

log = logging.getLogger("mw")


class UserMiddleware(BaseMiddleware):
    """يعمل على مستوى الرسائل والضغطات معاً."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        is_admin = user.id in settings.admin_ids
        data["is_admin"] = is_admin

        # نسجّل المستخدم في كل مرة (استعلام واحد رخيص: upsert)
        try:
            full_name = user.full_name or ""
            is_new = await users_repo.upsert_user(user.id, full_name, user.username)
            data["is_new_user"] = is_new
            if not is_admin and await users_repo.is_blocked(user.id):
                if isinstance(event, Message):
                    await event.answer(T.BLOCKED)
                elif isinstance(event, CallbackQuery):
                    await event.answer(T.BLOCKED, show_alert=True)
                return None
        except Exception as e:  # noqa: BLE001
            # لا نمنع الرد بسبب عطل مؤقت في القاعدة — نكمل بدون تسجيل
            log.warning("user middleware db error: %s", e)
            data.setdefault("is_new_user", False)

        t0 = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            dt = (time.perf_counter() - t0) * 1000
            if dt > 2000:
                log.warning("slow handler %.0f ms for user %s", dt, user.id)


class ErrorsMiddleware(BaseMiddleware):
    """يلتقط الأخطاء على مستوى Update كاملاً حتى لا يفقد تيليغرام التحديث بصمت."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as e:  # noqa: BLE001
            log.exception("unhandled error: %s", e)
            bot = data.get("bot")
            uid = None
            if isinstance(event, Update):
                if event.message:
                    uid = event.message.chat.id
                elif event.callback_query:
                    uid = event.callback_query.from_user.id   # لا نكتب في القناة — نراسل الضاغط في خاصّه
            if bot and uid and uid > 0:
                try:
                    await bot.send_message(uid, "حدث خطأ مؤقت 😕 — جرّب مرة ثانية، وإذا تكرر تواصل مع الدعم.")
                except Exception:  # noqa: BLE001
                    pass
            if bot and settings.admin_ids:
                try:
                    from app.services import channels
                    await channels.alert(
                        bot, f"⚠️ <b>خطأ غير متوقع</b>\n<code>{type(e).__name__}: {str(e)[:300]}</code>\nمستخدم: {uid}",
                    )
                except Exception:  # noqa: BLE001
                    pass
            return None
