"""أدوات مشتركة لمعالجات الأدمن — تجعل الأزرار تعمل من داخل قنوات الإدارة أيضاً.

المشكلة: حين يضغط الأدمن زراً على بطاقة داخل قناة، فإن «الحالة» (FSM) تُحسب على القناة لا على خاصّه،
وأي نص يكتبه بعدها يصل من خاصّه ⇒ لا يلتقطه المعالج. الحل: نضبط الحالة على سياق الخاص ونرسل السؤال هناك.
"""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery

OPEN_PRIVATE_FIRST = "افتح محادثة البوت في الخاص واضغط Start أولاً، ثم أعد المحاولة."
CONTINUE_IN_PRIVATE = "✍️ تابع في خاصّ البوت"


def in_channel(cb: CallbackQuery) -> bool:
    """هل ضُغط الزر خارج خاصّ الأدمن (قناة/مجموعة)؟"""
    msg = cb.message
    return bool(msg) and getattr(msg, "chat", None) is not None and msg.chat.id != cb.from_user.id


def private_state(cb: CallbackQuery, state: FSMContext) -> FSMContext:
    """سياق حالة خاصّ الأدمن (هو نفسه state إن كان الضغط في الخاص أصلاً)."""
    if not in_channel(cb):
        return state
    return FSMContext(storage=state.storage,
                      key=StorageKey(bot_id=cb.bot.id, chat_id=cb.from_user.id, user_id=cb.from_user.id))


async def reply(cb: CallbackQuery, text: str, kb=None):
    """رسالة مساعدة للأدمن: في الخاص دائماً (حتى لا نلوّث القناة). تعيد الرسالة أو None إن تعذّر."""
    try:
        return await cb.bot.send_message(cb.from_user.id, text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — الأدمن لم يفتح الخاص
        await cb.answer(OPEN_PRIVATE_FIRST, show_alert=True)
        return None


async def ask_input(cb: CallbackQuery, state: FSMContext, new_state: State, data: dict, prompt: str, kb=None) -> bool:
    """يبدأ خطوة تحتاج كتابة: يضبط الحالة على خاصّ الأدمن ويرسل السؤال هناك. يعيد False إن تعذّر."""
    st = private_state(cb, state)
    await st.set_state(new_state)
    await st.update_data(**data)
    m = await reply(cb, prompt, kb)
    if m is None:
        await st.clear()
        return False
    if in_channel(cb):
        await cb.answer(CONTINUE_IN_PRIVATE)
    else:
        await cb.answer()
    return True
