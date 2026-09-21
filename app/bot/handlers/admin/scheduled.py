"""إدخال محتوى الاشتراكات المجدولة من خاص الأدمن أو من بطاقة التنبيه."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.config import settings
from app.bot.handlers.admin import _common as C
from app.services import scheduled as SD

router = Router(name="admin_scheduled")
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))


class ScheduledAdmin(StatesGroup):
    seq = State()
    file = State()
    copy = State()


def _cancel(message: Message) -> bool:
    return bool(message.text and (message.text.startswith("/") or message.text in ("🏠 القائمة", "❌ إلغاء")))


def _file(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.document:
        return "document", message.document.file_id
    if message.video:
        return "video", message.video.file_id
    return None


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+):add$"))
async def cb_add(cb: CallbackQuery, state: FSMContext) -> None:
    sid = int(cb.data.split(":")[2])
    sub = await SD.detail(sid)
    if not sub:
        await cb.answer("الاشتراك غير موجود", show_alert=True)
        return
    await C.ask_input(
        cb, state, ScheduledAdmin.seq, {"subscription_id": sid},
        f"📤 <b>إضافة محتوى SUB-{sid}</b>\n\nأرسل رقم اليوم الذي تريد تعبئته (من 1 إلى {sub['total_items']}):",
        K.cancel_input("adm:panel"),
    )


@router.message(ScheduledAdmin.seq, F.text)
async def msg_seq(message: Message, state: FSMContext) -> None:
    if _cancel(message):
        await state.clear()
        await message.answer("تم الإلغاء.", reply_markup=K.home_bar())
        return
    data = await state.get_data()
    sub = await SD.detail(int(data.get("subscription_id") or 0))
    try:
        seq = int((message.text or "").strip())
    except ValueError:
        seq = 0
    if not sub or not 1 <= seq <= int(sub["total_items"]):
        await message.answer(f"أرسل رقماً بين 1 و{sub['total_items'] if sub else 'الحد المطلوب'}.")
        return
    await state.update_data(seq=seq)
    await state.set_state(ScheduledAdmin.file)
    await message.answer(f"📎 أرسل الآن تصميم اليوم {seq} كصورة أو ملف:", reply_markup=K.cancel_input("adm:panel"))


@router.message(ScheduledAdmin.file, F.photo | F.document | F.video)
async def msg_file(message: Message, state: FSMContext) -> None:
    data = _file(message)
    if not data:
        await message.answer("أرسل صورة أو ملف تصميم.")
        return
    kind, file_id = data
    await state.update_data(file_kind=kind, file_id=file_id)
    await state.set_state(ScheduledAdmin.copy)
    await message.answer("✍️ أرسل النص الكتابي المقابل لهذا التصميم الآن:", reply_markup=K.cancel_input("adm:panel"))


@router.message(ScheduledAdmin.file)
async def msg_file_wrong(message: Message) -> None:
    await message.answer("أرسل التصميم كصورة أو ملف أو فيديو.")


@router.message(ScheduledAdmin.copy, F.text)
async def msg_copy(message: Message, state: FSMContext) -> None:
    if _cancel(message):
        await state.clear()
        await message.answer("تم الإلغاء.", reply_markup=K.home_bar())
        return
    data = await state.get_data()
    try:
        item = await SD.add_content(int(data["subscription_id"]), int(data["seq"]), str(data["file_kind"]),
                                    str(data["file_id"]), message.text or "")
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.clear()
    await message.answer(f"✅ حُفظ التصميم والنص لليوم {item['seq']} في SUB-{data['subscription_id']}.", reply_markup=K.home_bar())


@router.message(ScheduledAdmin.copy)
async def msg_copy_wrong(message: Message) -> None:
    await message.answer("أرسل النص الكتابي برسالة نصية.")
