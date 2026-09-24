"""رمز الشراء (checkout token) — يمنع الخصم المكرر في كل معالجات الدفع.

المشكلة: aiogram يعالج التحديثات بالتوازي، فضغطتان سريعتان على «تأكيد» تقرآن نفس بيانات المعالج
قبل مسحها، وكل واحدة تُنشئ طلباً وتخصم. وأيضاً زر قديم قد يُضغط لاحقاً.

الحل: عند عرض الملخص نولّد رمزاً عشوائياً ونحفظه في بيانات المعالج (مرة واحدة لكل معالج).
orders_svc.confirm يقبل طلباً واحداً فقط لكل رمز (عمود orders.checkout_key عليه UNIQUE)؛
والضغطة الثانية تعيد الطلب الأول نفسه بعلامة duplicate فنجيب «طلبك مسجّل مسبقاً» بلا أي خصم.
"""

from __future__ import annotations

import secrets

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

DUPLICATE_TEXT = "✅ طلبك #ORD-{id} مسجّل مسبقاً — لم يُخصم أي مبلغ إضافي"


async def ensure_token(state: FSMContext) -> str:
    """يعيد رمز الشراء الحالي أو يولّد واحداً جديداً (يُستدعى عند عرض الملخص)."""
    d = await state.get_data()
    tok = d.get("checkout")
    if not tok:
        tok = secrets.token_hex(8)
        await state.update_data(checkout=tok)
    return tok


def key_for(cb: CallbackQuery, data: dict) -> str:
    """المفتاح النهائي المحفوظ مع الطلب.

    إن لم يوجد رمز (جلسة بدأت قبل هذا الإصدار) نستخدم رقم رسالة الملخص — ثابت لكل ضغطات نفس الرسالة.
    """
    tok = data.get("checkout")
    if tok:
        return f"chk-{cb.from_user.id}-{tok}"
    mid = cb.message.message_id if cb.message else 0
    return f"msg-{cb.from_user.id}-{mid}"


async def answer_duplicate(cb: CallbackQuery, order: dict) -> None:
    try:
        await cb.answer(DUPLICATE_TEXT.format(id=order.get("id")), show_alert=False)
    except Exception:  # noqa: BLE001 — الضغطة قد تكون أُجيبت/انتهت
        pass
