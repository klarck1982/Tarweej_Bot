"""أزرار القائمة الرئيسية — كل زر يفتح فرعه.

يعمل بالكامل: 📢 Meta (M0 → المعالج في meta_wizard.py)، ℹ️ المعلومات والأسعار، 💬 الأسئلة الشائعة، 💰 الرصيد (topup.py)، 📦 الطلبات (orders.py).
T0 / D0 معاينة — معالجاتها في الخطوتين 5 و6.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db import pool as db
from app.db.repo import events, orders as orders_repo, settings as settings_repo, users as users_repo
from app.services.pricing import fmt

router = Router(name="menu")


async def _guard_wizard(message: Message, state: FSMContext) -> None:
    """ضغط زر رئيسي في منتصف معالج = خروج نظيف من المعالج (القاعدة 1 في خريطة الأزرار)."""
    if await state.get_state():
        await state.clear()


async def _show(cb: CallbackQuery, text: str, kb) -> None:
    """من القائمة الملوّنة: نحرّر الرسالة نفسها بدل إرسال رسالة جديدة (شاشة واحدة تتبدّل)."""
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — رسالة بصورة أو قديمة
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


# ───────────── ✨ زر العنوان ─────────────

@router.callback_query(F.data == "nav:title")
async def cb_title(cb: CallbackQuery) -> None:
    await cb.answer(T.TITLE_TOAST, show_alert=True)


# ───────────── 📢 Meta ─────────────

async def _meta_screen(uid: int) -> tuple[str, object]:
    svc = await settings_repo.services()
    draft = await orders_repo.get_awaiting(uid)
    return T.meta_intro_v3(), K.meta_packages(enabled=svc["meta"], has_draft=bool(draft))


@router.message(F.text.in_({T.BTN_META, "📢 إعلان فيسبوك/إنستغرام"}))
async def m_meta(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    text, kb = await _meta_screen(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "nav:meta")
async def cb_nav_meta(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _meta_screen(cb.from_user.id)
    await _show(cb, text, kb)


@router.callback_query(F.data == "meta:diff")
async def cb_meta_diff(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.META_DIFF, reply_markup=K.meta_diff_back())
    await cb.answer()


# ───────────── ✈️ تيليغرام ─────────────

@router.message(F.text == T.BTN_TG)
async def m_tg(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    svc = await settings_repo.services()
    await message.answer(T.TG_INTRO, reply_markup=K.tg_tracks(svc["tg_ads"], svc["tg_post"]))


@router.callback_query(F.data == "nav:tg")
async def cb_nav_tg(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    svc = await settings_repo.services()
    await _show(cb, T.TG_INTRO, K.tg_tracks(svc["tg_ads"], svc["tg_post"]))


@router.callback_query(F.data == "tgp:start")
async def cb_tg_post_start(cb: CallbackQuery) -> None:
    """القنوات الشريكة — تُبنى في التسليم التالي؛ الزر مقفول حتى تُضاف أول قناة."""
    await events.log_event("tg_track_click", cb.from_user.id, track="tg_post")
    await cb.answer(T.TGP_SOON, show_alert=True)


# ───────────── 🎨 تصميم ─────────────

@router.message(F.text == T.BTN_DESIGN)
async def m_design(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    svc = await settings_repo.services()
    await message.answer(T.design_intro(), reply_markup=K.design_services(svc["addons"], svc["ai_reel"]))


@router.callback_query(F.data == "nav:design")
async def cb_nav_design(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    svc = await settings_repo.services()
    await _show(cb, T.design_intro(), K.design_services(svc["addons"], svc["ai_reel"]))


@router.callback_query(F.data == "add:svc:ai_reel")
async def cb_ai_reel(cb: CallbackQuery) -> None:
    svc = await settings_repo.services()
    if not svc["ai_reel"]:
        await events.log_event("ai_reel_interest", cb.from_user.id)
        await cb.message.answer(T.AI_REEL_SOON)
        await cb.answer()
        return
    await cb.answer(T.COMING_STEP.format(step=6).replace("<b>", "").replace("</b>", ""), show_alert=True)


@router.callback_query(F.data.startswith("add:svc:"))
async def cb_addon(cb: CallbackQuery) -> None:
    svc = await settings_repo.services()
    if not svc["addons"]:
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    await events.log_event("addon_click", cb.from_user.id, svc=cb.data.split(":")[-1])
    await cb.answer(T.COMING_STEP.format(step=6).replace("<b>", "").replace("</b>", ""), show_alert=True)


# ───────────── 💬 الدعم ─────────────

@router.message(F.text.in_({T.BTN_SUPPORT, "🆘 الدعم"}))
async def m_support(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    await message.answer(T.SUPPORT_MENU, reply_markup=K.support_menu())


@router.callback_query(F.data == "sup:menu")
async def cb_support(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(cb, T.SUPPORT_MENU, K.support_menu())
    return
    await cb.answer()


@router.callback_query(F.data == "sup:faq")
async def cb_faq(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.FAQ_MENU, reply_markup=K.faq_list())
    await cb.answer()


@router.callback_query(F.data.startswith("sup:faq:"))
async def cb_faq_item(cb: CallbackQuery) -> None:
    try:
        idx = int(cb.data.split(":")[-1])
        _, answer = T.FAQ[idx]
    except (ValueError, IndexError):
        await cb.answer()
        return
    await cb.message.edit_text(answer, reply_markup=K.faq_answer())
    await cb.answer()


@router.callback_query(F.data.in_({"sup:new", "sup:mine"}))
async def cb_ticket_soon(cb: CallbackQuery) -> None:
    contact = f"@{settings.support_username}" if settings.support_username else T.NO_CONTACT
    await cb.message.answer(T.TICKET_SOON.format(contact=contact), reply_markup=K.home_only())
    await cb.answer()


# ───────────── ℹ️ المعلومات ─────────────

@router.message(F.text.in_({T.BTN_INFO, T.BTN_INFO_LONG}))
async def m_info(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    await message.answer(T.INFO_MENU, reply_markup=K.info_menu())


@router.callback_query(F.data == "info:menu")
async def cb_info(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(cb, T.INFO_MENU, K.info_menu())
    return
    await cb.answer()


@router.callback_query(F.data == "info:ads")
async def cb_info_ads(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.prices_ads(), reply_markup=K.info_back())
    await cb.answer()


@router.callback_query(F.data == "info:design")
async def cb_info_design(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.prices_design(), reply_markup=K.info_back())
    await cb.answer()


@router.callback_query(F.data == "info:how")
async def cb_info_how(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.HOW_IT_WORKS, reply_markup=K.info_back(start_cta=True))
    await cb.answer()


@router.callback_query(F.data == "info:terms")
async def cb_info_terms(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.TERMS_SHORT, reply_markup=K.info_back())
    await cb.answer()


@router.callback_query(F.data == "info:about")
async def cb_info_about(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.ABOUT, reply_markup=K.info_back())
    await cb.answer()
