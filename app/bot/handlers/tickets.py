"""🎫 تذاكر الدعم عند العميل — v0.8.1."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.db.repo import orders as orders_repo, tickets as ticket_repo
from app.services import ticket_notify as TN

router = Router(name="tickets")


class ClientTicket(StatesGroup):
    message = State()
    reply = State()


def _file_from(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.video:
        return "video", message.video.file_id
    if message.document:
        return "document", message.document.file_id
    if message.animation:
        return "video", message.animation.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.voice:
        return "audio", message.voice.file_id
    return None


async def _open_ticket(cb: CallbackQuery, state: FSMContext, order_id: int | None = None,
                       kind: str = "question") -> None:
    if order_id is not None:
        order = await orders_repo.get(order_id)
        if not order or order["user_id"] != cb.from_user.id:
            await cb.answer("هذا الطلب غير موجود", show_alert=True)
            return
    ticket = await ticket_repo.create(cb.from_user.id, order_id=order_id, kind=kind)
    await state.set_state(ClientTicket.message)
    await state.update_data(ticket_id=ticket["id"], ticket_new=not bool(await ticket_repo.messages(ticket["id"], 1)))
    await cb.message.answer(T.TICKET_PROMPT, reply_markup=K.home_only())
    await cb.answer()


@router.callback_query(F.data == "sup:new")
async def cb_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    rows = await orders_repo.list_for_user(cb.from_user.id, limit=12)
    await cb.message.edit_text(T.TICKET_ORDER_PICK, reply_markup=K.ticket_order_choices(rows))
    await cb.answer()


@router.callback_query(F.data == "sup:mine")
async def cb_mine(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    items = await ticket_repo.list_for_user(cb.from_user.id)
    if not items:
        await cb.message.edit_text(T.TICKET_NO_OPEN, reply_markup=K.support_menu())
    else:
        await cb.message.edit_text("📂 <b>تذاكرك</b>", reply_markup=K.ticket_list(items))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^tck:open:order:(\d+)$"))
async def cb_open_order(cb: CallbackQuery, state: FSMContext) -> None:
    await _open_ticket(cb, state, int(cb.data.split(":")[-1]), "issue")


@router.callback_query(F.data == "tck:open:topup")
async def cb_open_topup(cb: CallbackQuery, state: FSMContext) -> None:
    await _open_ticket(cb, state, None, "topup")


@router.callback_query(F.data == "tck:open:general")
async def cb_open_general(cb: CallbackQuery, state: FSMContext) -> None:
    await _open_ticket(cb, state, None, "question")


@router.callback_query(F.data.regexp(r"^ord:help:(\d+)$"))
async def cb_order_help(cb: CallbackQuery, state: FSMContext) -> None:
    await _open_ticket(cb, state, int(cb.data.split(":")[-1]), "issue")


@router.callback_query(F.data.regexp(r"^tck:view:(\d+)$"))
async def cb_view(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[-1])
    ticket = await ticket_repo.get(tid)
    if not ticket or ticket["user_id"] != cb.from_user.id:
        await cb.answer("غير موجود", show_alert=True)
        return
    await state.clear()
    await cb.message.edit_text(TN.client_text(ticket, await ticket_repo.messages(tid)), reply_markup=K.ticket_view(ticket))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^tck:reply:(\d+)$"))
async def cb_reply(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[-1])
    ticket = await ticket_repo.get(tid)
    if not ticket or ticket["user_id"] != cb.from_user.id or ticket["status"] == "closed":
        await cb.answer("التذكرة مغلقة — افتح تذكرة جديدة", show_alert=True)
        return
    await state.set_state(ClientTicket.reply)
    await state.update_data(ticket_id=tid)
    await cb.message.answer(T.TICKET_REPLY_PROMPT.format(id=tid), reply_markup=K.home_only())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^tck:close:(\d+)$"))
async def cb_close(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[-1])
    ticket = await ticket_repo.get(tid)
    if not ticket or ticket["user_id"] != cb.from_user.id:
        await cb.answer("غير موجود", show_alert=True)
        return
    closed = await ticket_repo.close(tid, cb.from_user.id)
    await state.clear()
    if closed:
        await cb.message.edit_text(T.TICKET_CLOSED.format(id=tid), reply_markup=K.support_menu())
        await TN.refresh_admin(cb.bot, tid)
    await cb.answer()


async def _save_client_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = int(data.get("ticket_id") or 0)
    ticket = await ticket_repo.get(tid)
    if not ticket or ticket["user_id"] != message.from_user.id:
        await state.clear()
        await message.answer("انتهت جلسة التذكرة — افتح تذكرة جديدة من 💬 الدعم.", reply_markup=K.support_menu())
        return
    text = (message.text or message.caption or "").strip()
    if len(text) > 1500:
        await message.answer("الرسالة طويلة — الحد 1500 حرف.")
        return
    file_info = _file_from(message)
    if not text and not file_info:
        await message.answer("اكتب رسالة أو أرسل ملفاً 🙂")
        return
    was_empty = not bool(await ticket_repo.messages(tid, 1))
    saved = await ticket_repo.add_message(tid, message.from_user.id, False, text or None,
                                          file_info[1] if file_info else None,
                                          file_info[0] if file_info else None)
    await state.clear()
    if not saved:
        await message.answer("تعذّر حفظ التذكرة — حاول مرة ثانية.")
        return
    ticket = await ticket_repo.get(tid)
    if was_empty:
        order = f" بخصوص <b>#ORD-{ticket['order_id']}</b>" if ticket.get("order_id") else ""
        await message.answer(T.TICKET_CREATED.format(id=tid, order=order), reply_markup=K.ticket_view(ticket))
    else:
        await message.answer(T.TICKET_CLIENT_REPLY.format(id=tid, body=TN.message_body({"text": text, "file_id": file_info[1] if file_info else None,
                                                                                         "file_kind": file_info[0] if file_info else None})),
                             reply_markup=K.ticket_view(ticket))
    await TN.notify_admins(message.bot, tid)


@router.message(ClientTicket.message, F.text)
async def msg_new(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    await _save_client_message(message, state)


@router.message(ClientTicket.reply, F.text)
async def msg_reply(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    await _save_client_message(message, state)


@router.message(ClientTicket.message, F.photo | F.video | F.document | F.animation | F.audio | F.voice)
async def media_new(message: Message, state: FSMContext) -> None:
    await _save_client_message(message, state)


@router.message(ClientTicket.reply, F.photo | F.video | F.document | F.animation | F.audio | F.voice)
async def media_reply(message: Message, state: FSMContext) -> None:
    await _save_client_message(message, state)
