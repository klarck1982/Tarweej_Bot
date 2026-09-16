"""آخر راوتر يُسجَّل: أي رسالة أو ضغطة لم يلتقطها أحد — لا يضيع المستخدم أبداً (القاعدة 7)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T

router = Router(name="fallback")


@router.message(F.chat.type == "private")
async def any_message(message: Message, state: FSMContext, is_admin: bool = False) -> None:
    if await state.get_state():
        # داخل معالج ينتظر إدخالاً معيّناً — المعالجات نفسها تتعامل مع ذلك في خطواتها؛ هنا تذكير لطيف
        await message.answer("أكمل من الأزرار أعلاه 👆 أو أرسل /cancel للإلغاء.")
        return
    from app.bot.handlers.start import show_main_menu  # استيراد متأخر لتجنّب الدورة
    await message.answer(T.UNKNOWN_TEXT, reply_markup=K.home_bar())
    await show_main_menu(message, message.from_user.id, is_admin)


@router.callback_query()
async def any_callback(cb: CallbackQuery) -> None:
    # زر من إصدار قديم أو قسم لم يُبنَ بعد
    await cb.answer("هذا الزر غير متاح حالياً — افتح القائمة من جديد 🏠", show_alert=False)
