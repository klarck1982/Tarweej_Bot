"""H0 / H1 — /start، الشروط، القائمة الرئيسية، الأوامر العامة، والنص الحر."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.db.repo import events, users as users_repo
from app.services.pricing import fmt

router = Router(name="start")


async def show_main_menu(message: Message, user_id: int, is_admin: bool, greet_name: str | None = None) -> None:
    balance = await users_repo.get_balance(user_id)
    text = T.WELCOME_BACK.format(name=greet_name) if greet_name else T.MAIN_MENU.format(balance=fmt(balance))
    await message.answer(text, reply_markup=K.main_menu(is_admin))


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
    await show_main_menu(message, uid, is_admin, greet_name=message.from_user.first_name)


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
    await show_main_menu(cb.message, cb.from_user.id, is_admin, greet_name=cb.from_user.first_name)


@router.callback_query(F.data == "nav:home")
async def cb_home(cb: CallbackQuery, state: FSMContext, is_admin: bool = False) -> None:
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:  # noqa: BLE001 — الرسالة قد تكون قديمة (>48 ساعة) فلا تُحذف
        await cb.message.edit_reply_markup(reply_markup=None)
    await show_main_menu(cb.message, cb.from_user.id, is_admin)
    await cb.answer()


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, is_admin: bool = False) -> None:
    await state.clear()
    await show_main_menu(message, message.from_user.id, is_admin)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, is_admin: bool = False) -> None:
    had = await state.get_state()
    await state.clear()
    await message.answer(T.CANCELLED if had else T.NOTHING_TO_CANCEL, reply_markup=K.main_menu(is_admin))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(T.SUPPORT_MENU, reply_markup=K.support_menu())


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """يعرض معرّفك — تحتاجه لملء ADMIN_IDS."""
    await message.answer(f"🆔 معرّفك: <code>{message.from_user.id}</code>")
