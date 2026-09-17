"""H0 / H1 — /start، الشروط، القائمة الرئيسية، الأوامر العامة، والنص الحر."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import VERSION
from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db.repo import events, users as users_repo
from app.services.pricing import fmt

router = Router(name="start")


async def show_main_menu(message: Message, user_id: int, is_admin: bool, greet_name: str | None = None,
                         greet: str | None = None, edit: bool = False) -> None:
    """القائمة الرئيسية = رسالة واحدة بأزرار ملوّنة (تصميم Ichancy) + شريط سفلي بزر 🏠 فقط."""
    balance = await users_repo.get_balance(user_id)
    text = T.MAIN_MENU
    if greet:
        text = f"{greet}\n\n{text}"
    elif greet_name:
        text = f"{T.WELCOME_BACK.format(name=greet_name)}\n\n{text}"
    attention = 0
    if is_admin:
        try:
            from app.db.repo import orders as orders_repo, topups as topups_repo
            from app.services import nour
            attention = await topups_repo.count_pending() + await orders_repo.count_attention(nour.is_dry_run())
        except Exception:  # noqa: BLE001
            attention = 0
    kb = K.main_menu(is_admin, fmt(balance), attention)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except Exception:  # noqa: BLE001 — رسالة بصورة/قديمة: نرسل جديدة
            pass
    await message.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, is_admin: bool = False,
                    is_new_user: bool = False) -> None:
    await state.clear()
    uid = message.from_user.id
    # رابط إحالة: /start ref_123  (يُستخدم في الخطوة 6)
    if command.args and command.args.startswith("ref_") and is_new_user:
        await events.log_event("referral_start", uid, ref=command.args)

    if not await users_repo.has_accepted_terms(uid):
        await events.log_event("start_new", uid)
        await message.answer(T.WELCOME_NEW, reply_markup=K.welcome())
        return
    # الشريط السفلي يُثبَّت مع رسالة الترحيب، ثم القائمة الملوّنة
    await message.answer(T.WELCOME_BACK.format(name=message.from_user.first_name), reply_markup=K.home_bar())
    await show_main_menu(message, uid, is_admin)


@router.callback_query(F.data == "nav:terms")
async def cb_terms(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.TERMS_SHORT, reply_markup=K.terms_back())
    await cb.answer()


@router.callback_query(F.data == "nav:accept")
async def cb_accept(cb: CallbackQuery, is_admin: bool = False) -> None:
    await users_repo.accept_terms(cb.from_user.id)
    await events.log_event("terms_accepted", cb.from_user.id)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("أهلاً وسهلاً 🌟")
    # أول دخول: ترحيب "هلا" + تثبيت الشريط السفلي، ثم القائمة الملوّنة
    await cb.message.answer(T.WELCOME_FIRST.format(name=cb.from_user.first_name), reply_markup=K.home_bar())
    await show_main_menu(cb.message, cb.from_user.id, is_admin)


@router.callback_query(F.data == "nav:home")
async def cb_home(cb: CallbackQuery, state: FSMContext, is_admin: bool = False) -> None:
    """🏠 داخل أي شاشة: تتحوّل الرسالة نفسها إلى القائمة الرئيسية (بلا رسائل جديدة)."""
    await state.clear()
    await show_main_menu(cb.message, cb.from_user.id, is_admin, edit=True)
    await cb.answer()


@router.message(Command("menu"))
@router.message(F.text == T.BTN_HOME)
async def cmd_menu(message: Message, state: FSMContext, is_admin: bool = False) -> None:
    await state.clear()
    await show_main_menu(message, message.from_user.id, is_admin)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, is_admin: bool = False) -> None:
    had = await state.get_state()
    await state.clear()
    await message.answer(T.CANCELLED if had else T.NOTHING_TO_CANCEL, reply_markup=K.home_bar())
    await show_main_menu(message, message.from_user.id, is_admin)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(T.SUPPORT_MENU, reply_markup=K.support_menu())


def _mask(i: int) -> str:
    s = str(i)
    return s if len(s) <= 6 else f"{s[:3]}…{s[-3:]}"


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """يعرض معرّفك + هل تعرفك هذه النسخة من البوت كأدمن (تشخيص ADMIN_IDS)."""
    uid = message.from_user.id
    if uid in settings.admin_ids:
        status = "✅ أنت مسجّل كأدمن في هذه النسخة."
    else:
        loaded = "، ".join(_mask(a) for a in settings.admin_ids) or "لا شيء"
        status = (
            "❌ غير مسجّل كأدمن في النسخة الشغّالة.\n"
            f"المعرّفات المحمّلة من ADMIN_IDS الآن: <code>{loaded}</code>\n"
            f"الوضع: <code>{settings.mode}</code> — الإصدار <code>{VERSION}</code>"
        )
    await message.answer(f"🆔 معرّفك: <code>{uid}</code>\n{status}")
