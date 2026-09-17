"""A2 — الطلبات عند الأدمن: القائمة، البطاقة، إعادة الإرسال، المزامنة، المحاكاة 🧪، الاسترداد، المراسلة، المعرّف الاحتياطي."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.bot.handlers.admin import _common as C
from app.config import settings
from app.db.repo import events, orders as repo, settings as settings_repo
from app.services import nour, order_notify as ON, orders as orders_svc, pricing as P
from app.services import validators as V
from app.services.pricing import fmt

router = Router(name="admin_orders")
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))


class AdminOrder(StatesGroup):
    refund_reason = State()
    message_user = State()
    fallback_username = State()


async def _list_view() -> tuple[str, object]:
    rows = await repo.list_open()
    if not rows:
        return T.ADMIN_ORDERS_EMPTY, K.admin_back()
    data = []
    for o in rows:
        label = f"{orders_svc.STATUS_ICON.get(o['status'], '•')} #ORD-{o['id']} · {fmt(o['price_usd'])} · {ON.pkg_label(o['spec'])} · {(o.get('user_name') or '')[:16]}"
        data.append((o["id"], label[:60]))
    return T.ADMIN_ORDERS_LIST.format(n=len(rows)), K.admin_orders_list(data)


@router.callback_query(F.data == "adm:orders")
async def cb_list(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _list_view()
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


async def _send_card(cb: CallbackQuery, oid: int, edit: bool = False) -> None:
    o = await repo.get(oid)
    if not o:
        await cb.answer("غير موجود", show_alert=True)
        return
    media_count = len(await repo.media(oid))
    text = await ON.admin_card_text(o, media_count)
    kb = K.admin_order_card(o, nour.is_dry_run(), media_count)
    if edit:
        try:
            await cb.message.edit_text(text, reply_markup=kb)
            return
        except Exception:  # noqa: BLE001
            pass
    dest = cb.from_user.id if C.in_channel(cb) else cb.message.chat.id
    m = await cb.bot.send_message(dest, text, reply_markup=kb)
    ids = list(o.get("admin_msg_ids") or [])
    ids.append([dest, m.message_id])
    await repo.set_messages(oid, admin_msg_ids=ids[-6:])


@router.callback_query(F.data.regexp(r"^adm:ord:(\d+):view$"))
async def cb_view(cb: CallbackQuery) -> None:
    await _send_card(cb, int(cb.data.split(":")[2]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:ord:(\d+):media$"))
async def cb_media(cb: CallbackQuery) -> None:
    from app.bot.handlers.orders import send_media
    # من داخل القناة: الملفات تُرسل إلى خاصّ الأدمن حتى لا تزدحم القناة
    await send_media(cb, int(cb.data.split(":")[2]), dest=cb.from_user.id if C.in_channel(cb) else None)
    if C.in_channel(cb):
        await cb.answer("📎 أُرسلت إلى خاصّك")
    else:
        await cb.answer()


# ───────────── إعادة الإرسال / المزامنة ─────────────

@router.callback_query(F.data.regexp(r"^adm:ord:(\d+):submit$"))
async def cb_submit(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await orders_svc.submit(oid)
    if o and o["status"] == "submitted":
        await cb.answer("📨 أُرسل — nour_id " + str(o.get("nour_id")))
    else:
        await cb.answer(("لم يُرسل: " + (o.get("note") or "")) [:190], show_alert=True)
    await ON.refresh_admin_cards(cb.bot, oid)


@router.callback_query(F.data.regexp(r"^adm:ord:(\d+):sync$"))
async def cb_sync(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o, changed = await orders_svc.sync_one(oid)
    await cb.answer("تغيّرت الحالة ✅" if changed else "لا جديد من نور")
    if changed and o:
        await ON.push_user_status(cb.bot, o)
    await ON.refresh_admin_cards(cb.bot, oid)


# ───────────── 🧪 محاكاة نور (وضع DRY RUN فقط) ─────────────

@router.callback_query(F.data.regexp(r"^adm:ord:(\d+):sim:(\w+)$"))
async def cb_sim(cb: CallbackQuery) -> None:
    if not nour.is_dry_run():
        await cb.answer("المحاكاة متاحة في الوضع التجريبي فقط", show_alert=True)
        return
    parts = cb.data.split(":")
    oid, nour_status = int(parts[2]), parts[4]
    if nour_status not in nour.NOUR_STATUSES:
        await cb.answer()
        return
    o, changed = await orders_svc.apply_nour_status(oid, nour_status, {"simulated": True, "status": nour_status})
    await events.log_event("order_sim", cb.from_user.id, oid, to=nour_status)
    await cb.answer(f"🧪 نور ← {nour_status}" + (" — أُبلغ العميل" if changed else ""))
    if changed and o:
        await ON.push_user_status(cb.bot, o)
    await ON.refresh_admin_cards(cb.bot, oid)


# ───────────── استرداد ─────────────

@router.callback_query(F.data.regexp(r"^adm:ord:(\d+):refund$"))
async def cb_refund(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    o = await repo.get(oid)
    if not o or o["status"] in repo.FINAL_STATUSES or o["status"] == "awaiting_payment":
        await cb.answer("هذا الطلب مغلق", show_alert=True)
        return
    await C.ask_input(cb, state, AdminOrder.refund_reason, {"oid": oid},
                      T.ADMIN_ORDER_REFUND_CONFIRM.format(price=fmt(o["price_usd"] - o.get("refunded_usd", 0)), id=oid),
                      K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.refund_reason, F.text)
async def msg_refund(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    o = await orders_svc.refund(data["oid"], reason=message.text.strip(), new_status="refunded", admin_id=message.from_user.id)
    if not o:
        await message.answer("لم يُنفَّذ الاسترداد (الطلب مغلق أو مُسترد سابقاً).")
        return
    await message.answer(f"↩️ أُعيد {fmt(o['refunded_usd'])} للعميل — #ORD-{o['id']} مغلق.")
    await ON.push_user_status(message.bot, o, reason=message.text.strip())
    await ON.refresh_admin_cards(message.bot, o["id"])


# ───────────── مراسلة العميل ─────────────

@router.callback_query(F.data.regexp(r"^adm:ord:(\d+):msg$"))
async def cb_msg(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    o = await repo.get(oid)
    if not o:
        await cb.answer()
        return
    await C.ask_input(cb, state, AdminOrder.message_user, {"uid": o["user_id"], "oid": oid},
                      f"✍️ اكتب رسالتك للعميل {ON.esc(o.get('user_name'))} بخصوص #ORD-{oid}:",
                      K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.message_user, F.text)
async def msg_msg(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    try:
        await message.bot.send_message(
            data["uid"], f"💬 <b>رسالة من الدعم بخصوص طلبك #ORD-{data['oid']}:</b>\n{ON.esc(message.text)}",
            reply_markup=K.support_menu(),
        )
        await message.answer("✅ أُرسلت.")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"تعذّر الإرسال: {e}")


# ───────────── المعرّف الاحتياطي ─────────────

@router.callback_query(F.data == "adm:fallback")
async def cb_fallback(cb: CallbackQuery, state: FSMContext) -> None:
    cur = await settings_repo.get("admin_fallback_username", "") or ""
    await state.set_state(AdminOrder.fallback_username)
    await cb.message.answer(T.ADMIN_FALLBACK_EDIT.format(current=("@" + cur) if cur else "غير مضبوط"),
                            reply_markup=K.cancel_input("adm:settings"))
    await cb.answer()


@router.message(AdminOrder.fallback_username, F.text)
async def msg_fallback(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    u = V.clean_username(message.text)
    if not u:
        await message.answer("المعرّف مو صالح — 5 أحرف على الأقل، أحرف إنجليزية وأرقام و _ فقط (بدون @).")
        return
    await state.clear()
    await settings_repo.set_("admin_fallback_username", u)
    await events.log_event("fallback_username_set", message.from_user.id, username=u)
    await message.answer(T.ADMIN_FALLBACK_SAVED.format(username=u), reply_markup=K.admin_settings_menu())
