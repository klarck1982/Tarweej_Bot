"""أزرار بعرض الشاشة كاملاً — لماذا تبدو القائمة «جانبية» وكيف نصلحها.

تيليغرام يجعل عرض لوحة الأزرار (inline) مساوياً لعرض فقاعة الرسالة، وعرض الفقاعة يتبع أطول سطر نصّ فيها.
رسالة قصيرة («اختر الخدمة 👇») ⇒ فقاعة ضيقة ⇒ أزرار ضيقة تبدو كقائمة جانبية — على الهاتف والحاسوب معاً.
لا توجد خاصية رسمية لتحديد عرض الأزرار، والحل المعتمد في البوتات الاحترافية: سطر «حشو» غير مرئي
(Braille Pattern Blank U+2800) يُلحق بالنص فيمدّ الفقاعة إلى أقصى عرض، فتتمدد الأزرار معها.

هذه الطبقة تفعل ذلك تلقائياً لكل رسالة نصية معها أزرار inline — لا حاجة لتعديل أي شاشة.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import EditMessageText, SendMessage, TelegramMethod
from aiogram.methods.base import Response, TelegramType
from aiogram.types import InlineKeyboardMarkup

# 40 حرفاً ≈ 320–400 بكسل: يلتفّ على الهاتف (سطر فارغ غير مرئي أو اثنان) ويصل لأقصى عرض على الحاسوب.
FILL_CHARS = 40
FILL = "\u200b".join("\u2800" * FILL_CHARS)  # فاصل صفري العرض بين الحروف ليلتفّ السطر بسلاسة
_MARK = FILL[:3]
MAX_TEXT = 4096


def widen(text: str | None) -> str | None:
    """يلحق الحشو غير المرئي بنهاية آخر سطر (لا يضيف سطراً جديداً — أقل ارتفاعاً على الهاتف)."""
    if not text or _MARK in text:
        return text
    body = text.rstrip()
    if len(body) + len(FILL) + 1 > MAX_TEXT:
        return text
    return f"{body} {FILL}"


class WideButtonsMiddleware(BaseRequestMiddleware):
    """تعمل قبل إرسال أي طلب إلى تيليغرام: تُوسّع نص SendMessage / EditMessageText إذا كانت معه أزرار inline."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if isinstance(method, (SendMessage, EditMessageText)) and isinstance(method.reply_markup, InlineKeyboardMarkup):
            method = method.model_copy(update={"text": widen(method.text)})
        return await make_request(bot, method)
