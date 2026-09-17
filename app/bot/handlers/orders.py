"""O — طلباتي: القائمة، تفاصيل الطلب، الملفات، المساعدة، إعادة الطلب."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, InputMediaVideo, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db.repo import orders as repo
from app.services import order_notify as ON, orders as orders_svc, pricing as P, targeting as TG
from app.services.pricing import fmt

router = Router(name="orders")
PAGE = 8


async def orders_view(uid: int, page: int = 1) -> tuple[str, object]:
    total = await repo.count_for_user(uid)
    draft = await repo.get_awaiting(uid)
    if not total:
        return T.ORDERS_EMPTY.format(trial=fmt(P.META_BY_CODE["trial"].price)), K.orders_empty()
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = min(max(1, page), pages)
    rows = await repo.list_for_user(uid, PAGE, (page - 1) * PAGE)
    data = []
    for o in rows:
        spec = o["spec"]
        label = f"{orders_svc.STATUS_ICON.get(o['status'], '•')} #ORD-{o['id']} · {ON.pkg_label(spec)} · {fmt(o['price_usd'])} · {orders_svc.STATUS_NAME.get(o['status'], '')}"
        data.append((o["id"], label[:60]))
    return T.ORDERS_LIST, K.orders_list(data, page, pages, draft["id"] if draft else None)


@router.message(Command("orders"))
@router.message(F.text == T.BTN_ORDERS)
async def m_orders(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await state.clear()
    text, kb = await orders_view(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "nav:orders")
@router.callback_query(F.data.startswith("ord:list:"))
async def cb_orders(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    page = int(cb.data.split(":")[2]) if cb.data.startswith("ord:list:") else 1
    text, kb = await orders_view(cb.from_user.id, page)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


def order_text(o: dict) -> str:
    spec = o["spec"]
    tl = []
    if o.get("submitted_at"):
        tl.append(f"أُرسل للتنفيذ: {ON._when(o['submitted_at'])}")
    if o.get("started_at"):
        tl.append(f"انطلق: {ON._when(o['started_at'])}")
    if o.get("completed_at"):
        tl.append(f"اكتمل: {ON._when(o['completed_at'])}")
    timeline = ("\n🕒 " + " · ".join(tl)) if tl else ""
    return T.ORDER_VIEW.format(
        icon=orders_svc.STATUS_ICON.get(o["status"], "•"), id=o["id"], status=orders_svc.STATUS_NAME.get(o["status"], o["status"]),
        pkg=ON.pkg_label(spec), platform=TG.PLATFORM_NAME.get(spec.get("platform"), ""), geo=ON.geo_label(spec),
        daily=fmt(spec["daily"]), days=P.days_word(spec["days"]), budget=fmt(P.money(P.D(str(spec["daily"])) * int(spec["days"]))),
        price=fmt(o["price_usd"]), link=ON.esc(spec.get("link")) or "—", created=ON._when(o.get("created_at")),
        timeline=timeline, hint=T.ORDER_HINTS.get(o["status"], ""),
    )


@router.callback_query(F.data.startswith("ord:view:"))
async def cb_view(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    o = await repo.get(oid)
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer("غير موجود", show_alert=True)
        return
    await state.clear()
    o["media_count"] = len(await repo.media(oid))
    text = order_text(o)
    try:
        await cb.message.edit_text(text, reply_markup=K.order_view(o))
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=K.order_view(o))
    await cb.answer()


@router.callback_query(F.data.startswith("ord:media:"))
async def cb_media(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await repo.get(oid)
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer()
        return
    await send_media(cb, oid)
    await cb.answer()


async def send_media(cb: CallbackQuery, oid: int) -> None:
    items = await repo.media(oid)
    if not items:
        await cb.answer("لا توجد ملفات", show_alert=True)
        return
    group = []
    for it in items:
        if it["kind"] == "photo":
            group.append(InputMediaPhoto(media=it["file_id"]))
        elif it["kind"] == "video":
            group.append(InputMediaVideo(media=it["file_id"]))
        else:
            await cb.message.answer_document(it["file_id"], caption=f"ملف #ORD-{oid}")
    if len(group) == 1:
        m = group[0]
        if isinstance(m, InputMediaPhoto):
            await cb.message.answer_photo(m.media, caption=f"ملفات #ORD-{oid}")
        else:
            await cb.message.answer_video(m.media, caption=f"ملفات #ORD-{oid}")
    elif group:
        await cb.message.answer_media_group(group)


@router.callback_query(F.data.startswith("ord:help:"))
async def cb_help(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    contact = f"@{settings.support_username}" if settings.support_username else T.NO_CONTACT
    await cb.message.answer(
        f"💬 <b>مساعدة بخصوص #ORD-{oid}</b>\nالتذاكر داخل البوت تُفعَّل في الخطوة 6 — حالياً تواصل مباشرة: {contact}\n"
        f"واذكر رقم الطلب <code>ORD-{oid}</code>.",
        reply_markup=K.support_menu(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ord:renew:"))
async def cb_renew(cb: CallbackQuery, state: FSMContext) -> None:
    """إعادة الطلب بنفس الإعدادات: نملأ المعالج ونقفز للملخص."""
    oid = int(cb.data.split(":")[2])
    o = await repo.get(oid)
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer()
        return
    spec = o["spec"]
    from app.bot.handlers.meta_wizard import _show_summary
    from app.services import validators as V
    await state.clear()
    # تجديد حزمة «انطلاقة متجر» = إعادة الإعلان فقط (باقة نمو) — النص والتصميم صارا عنده
    pkg = P.BUNDLE_STORE_LAUNCH["package"] if spec.get("bundle") else spec.get("pkg")
    await state.update_data(
        pkg=pkg, daily=spec["daily"], days=int(spec["days"]), platform=spec["platform"], goal=spec.get("goal"),
        country=spec["country"], provinces=spec.get("provinces") or [], gender=spec.get("gender", "all"),
        age_min=spec.get("age_min", 18), age_max=spec.get("age_max", 65), link=spec.get("link"), desc=spec.get("desc"),
        media=[], addons=[a for a in (spec.get("addons") or []) if not spec.get("bundle")], bundle=False,
        whatsapp=spec.get("whatsapp"), tg_username=V.clean_username(cb.from_user.username) or spec.get("tg_username"),
        uname_skipped=True, renew_of=oid,
    )
    await _show_summary(cb, state, new_message=True)
    await cb.answer("🔁 نفس الإعدادات — راجع وأكّد")
