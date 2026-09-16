"""أزرار اللوحة الرئيسية السبعة — كل زر يفتح فرعه.

في الخطوة 1 تعمل بالكامل: ℹ️ المعلومات والأسعار، 🆘 الأسئلة الشائعة، 💰 عرض الرصيد، 📦 الطلبات (فارغة).
شاشات الاختيار الأولى للخدمات (M0 / T0 / D0) تظهر كمعاينة، والمعالجات نفسها تأتي في الخطوات 2–6.
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
from app.db.repo import events, settings as settings_repo, users as users_repo
from app.services.pricing import fmt

router = Router(name="menu")


async def _guard_wizard(message: Message, state: FSMContext) -> None:
    """ضغط زر رئيسي في منتصف معالج = خروج نظيف من المعالج (القاعدة 1 في خريطة الأزرار)."""
    if await state.get_state():
        await state.clear()


# ───────────── 📢 Meta ─────────────

@router.message(F.text == T.BTN_META)
async def m_meta(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    svc = await settings_repo.services()
    await message.answer(T.meta_intro(), reply_markup=K.meta_packages(enabled=svc["meta"]))


@router.callback_query(F.data == "meta:pkgs")
async def cb_meta_pkgs(cb: CallbackQuery) -> None:
    svc = await settings_repo.services()
    await cb.message.edit_text(T.meta_intro(), reply_markup=K.meta_packages(enabled=svc["meta"]))
    await cb.answer()


@router.callback_query(F.data == "meta:diff")
async def cb_meta_diff(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.META_DIFF, reply_markup=K.meta_diff_back())
    await cb.answer()


@router.callback_query(F.data.startswith("meta:pkg:"))
async def cb_meta_pkg(cb: CallbackQuery) -> None:
    svc = await settings_repo.services()
    if not svc["meta"]:
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    await events.log_event("meta_pkg_click", cb.from_user.id, pkg=cb.data.split(":")[-1])
    await cb.answer(T.COMING_STEP.format(step=3).replace("<b>", "").replace("</b>", ""), show_alert=True)


# ───────────── ✈️ تيليغرام ─────────────

@router.message(F.text == T.BTN_TG)
async def m_tg(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    svc = await settings_repo.services()
    await message.answer(T.TG_INTRO, reply_markup=K.tg_tracks(svc["tg_ads"], svc["tg_post"]))


@router.callback_query(F.data.in_({"tga:start", "tgp:start"}))
async def cb_tg_start(cb: CallbackQuery) -> None:
    svc = await settings_repo.services()
    key = "tg_ads" if cb.data == "tga:start" else "tg_post"
    if not svc[key]:
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    await events.log_event("tg_track_click", cb.from_user.id, track=key)
    await cb.answer(T.COMING_STEP.format(step=5).replace("<b>", "").replace("</b>", ""), show_alert=True)


# ───────────── 🎨 تصميم ─────────────

@router.message(F.text == T.BTN_DESIGN)
async def m_design(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    svc = await settings_repo.services()
    await message.answer(T.design_intro(), reply_markup=K.design_services(svc["addons"], svc["ai_reel"]))


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


# ───────────── 💰 الرصيد ─────────────

async def _balance_text(uid: int) -> str:
    balance = await users_repo.get_balance(uid)
    last = await db.fetchrow(
        "SELECT type, amount_usd, created_at FROM ledger WHERE user_id = $1 ORDER BY id DESC LIMIT 1", uid
    )
    if last:
        sign = "+" if last["amount_usd"] > 0 else "−"
        kinds = {"topup": "شحن", "order_charge": "طلب", "refund": "استرداد", "referral": "إحالة", "adjustment": "تعديل"}
        when = last["created_at"].strftime("%d/%m %H:%M")
        last_txt = f"آخر عملية: {kinds.get(last['type'], last['type'])} {sign}{fmt(abs(last['amount_usd']))} — {when}"
    else:
        last_txt = T.BALANCE_NO_TX
    return T.BALANCE.format(balance=fmt(balance), last=last_txt)


@router.message(Command("balance"))
@router.message(F.text == T.BTN_BALANCE)
async def m_balance(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    await message.answer(await _balance_text(message.from_user.id), reply_markup=K.balance_menu())


@router.callback_query(F.data == "bal:menu")
async def cb_balance(cb: CallbackQuery) -> None:
    await cb.message.edit_text(await _balance_text(cb.from_user.id), reply_markup=K.balance_menu())
    await cb.answer()


@router.callback_query(F.data == "bal:topup")
async def cb_topup(cb: CallbackQuery) -> None:
    await events.log_event("topup_click", cb.from_user.id)
    await cb.message.answer(T.TOPUP_SOON, reply_markup=K.home_only())
    await cb.answer()


@router.callback_query(F.data.startswith("bal:hist:"))
async def cb_history(cb: CallbackQuery) -> None:
    rows = await db.fetch(
        "SELECT type, amount_usd, created_at FROM ledger WHERE user_id = $1 ORDER BY id DESC LIMIT 10", cb.from_user.id
    )
    if not rows:
        await cb.answer("ما في عمليات بعد", show_alert=True)
        return
    kinds = {"topup": "شحن", "order_charge": "طلب", "refund": "استرداد", "referral": "إحالة", "adjustment": "تعديل"}
    lines = ["📜 <b>آخر العمليات:</b>"]
    for r in rows:
        sign = "+" if r["amount_usd"] > 0 else "−"
        lines.append(f"{sign}{fmt(abs(r['amount_usd']))}  {kinds.get(r['type'], r['type'])} — {r['created_at']:%d/%m}")
    await cb.message.edit_text("\n".join(lines), reply_markup=K.back("bal:menu"))
    await cb.answer()


# ───────────── 📦 الطلبات ─────────────

@router.message(Command("orders"))
@router.message(F.text == T.BTN_ORDERS)
async def m_orders(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    n = await db.fetchval("SELECT count(*) FROM orders WHERE user_id = $1 AND status <> 'draft'", message.from_user.id)
    if not n:
        await message.answer(T.ORDERS_EMPTY, reply_markup=K.home_only())
        return
    await message.answer(T.COMING_STEP.format(step=4), reply_markup=K.home_only())


# ───────────── 🆘 الدعم ─────────────

@router.message(F.text == T.BTN_SUPPORT)
async def m_support(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    await message.answer(T.SUPPORT_MENU, reply_markup=K.support_menu())


@router.callback_query(F.data == "sup:menu")
async def cb_support(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.SUPPORT_MENU, reply_markup=K.support_menu())
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

@router.message(F.text == T.BTN_INFO)
async def m_info(message: Message, state: FSMContext) -> None:
    await _guard_wizard(message, state)
    await message.answer(T.INFO_MENU, reply_markup=K.info_menu())


@router.callback_query(F.data == "info:menu")
async def cb_info(cb: CallbackQuery) -> None:
    await cb.message.edit_text(T.INFO_MENU, reply_markup=K.info_menu())
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
