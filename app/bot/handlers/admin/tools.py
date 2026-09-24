"""v0.8.1 — أدوات الأدمن اليومية: 🎫 تذاكر، 📣 بث، 👤 بحث ورصيد."""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.bot.handlers.admin import _common as C
from app.config import settings
from app.db.repo import events, orders as orders_repo, tickets as ticket_repo, users as users_repo
from app.services import money, ticket_notify as TN
from app.services.pricing import fmt, money as money_value

log = logging.getLogger(__name__)

router = Router(name="admin_tools")
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))


class AdminTools(StatesGroup):
    ticket_reply = State()
    search = State()
    balance_input = State()
    broadcast_text = State()
    broadcast_photo = State()
    broadcast_audience = State()
    broadcast_confirm = State()


def _cancelled_text(message: Message) -> bool:
    return bool(message.text and (message.text.startswith("/") or message.text in T.MAIN_BUTTONS))


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


# ───────────────── 🎫 التذاكر ─────────────────

@router.callback_query(F.data == "adm:tickets")
async def cb_tickets(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    rows = await ticket_repo.list_admin()
    text = T.ADMIN_TICKET_EMPTY if not rows else T.ADMIN_TICKET_LIST.format(n=len(rows))
    kb = K.admin_back() if not rows else K.ticket_list(rows, admin=True)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        if C.in_channel(cb):
            await C.reply(cb, text, kb)
        else:
            await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:tck:(\d+):view$"))
async def cb_ticket_view(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tid = int(cb.data.split(":")[2])
    ticket = await ticket_repo.get(tid)
    if not ticket:
        await cb.answer("التذكرة غير موجودة", show_alert=True)
        return
    text = TN.admin_text(ticket, await ticket_repo.messages(tid))
    try:
        await cb.message.edit_text(text, reply_markup=K.admin_ticket_card(ticket))
    except Exception:
        if C.in_channel(cb):
            await C.reply(cb, text, K.admin_ticket_card(ticket))
        else:
            await cb.message.answer(text, reply_markup=K.admin_ticket_card(ticket))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:tck:(\d+):reply$"))
async def cb_ticket_reply(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    ticket = await ticket_repo.get(tid)
    if not ticket or ticket["status"] == "closed":
        await cb.answer("التذكرة مغلقة", show_alert=True)
        return
    await C.ask_input(cb, state, AdminTools.ticket_reply, {"ticket_id": tid},
                      T.ADMIN_TICKET_REPLY_PROMPT.format(id=tid), K.cancel_input("adm:cancel_input"))


async def _save_admin_ticket_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tid = int(data.get("ticket_id") or 0)
    ticket = await ticket_repo.get(tid)
    if not ticket:
        await state.clear()
        await message.answer("التذكرة غير موجودة.")
        return
    text = (message.text or message.caption or "").strip()
    if len(text) > 1500:
        await message.answer("الرد طويل — الحد 1500 حرف.")
        return
    file_info = _file_from(message)
    if not text and not file_info:
        await message.answer("اكتب الرد أو أرسل ملفاً 🙂")
        return
    saved = await ticket_repo.add_message(tid, message.from_user.id, True, text or None,
                                          file_info[1] if file_info else None,
                                          file_info[0] if file_info else None)
    await state.clear()
    if not saved:
        await message.answer("تعذّر حفظ الرد.")
        return
    await TN.notify_client(message.bot, tid, body=text or "وصلتك رسالة من الفريق.",
                           message={"file_id": file_info[1], "file_kind": file_info[0]} if file_info else None)
    await message.answer(T.ADMIN_TICKET_REPLY_DONE)
    await TN.refresh_admin(message.bot, tid)


@router.message(AdminTools.ticket_reply, F.text)
async def msg_ticket_reply(message: Message, state: FSMContext) -> None:
    if _cancelled_text(message):
        await state.clear()
        return
    await _save_admin_ticket_message(message, state)


@router.message(AdminTools.ticket_reply, F.photo | F.video | F.document | F.animation | F.audio | F.voice)
async def media_ticket_reply(message: Message, state: FSMContext) -> None:
    await _save_admin_ticket_message(message, state)


@router.callback_query(F.data.regexp(r"^adm:tck:(\d+):close$"))
async def cb_ticket_close(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    ticket = await ticket_repo.get(tid)
    if not ticket:
        await cb.answer("غير موجود", show_alert=True)
        return
    closed = await ticket_repo.close(tid, cb.from_user.id)
    await state.clear()
    if closed:
        try:
            await cb.bot.send_message(ticket["user_id"], T.TICKET_CLOSED.format(id=tid), reply_markup=K.support_menu())
        except Exception:
            pass
        await TN.refresh_admin(cb.bot, tid)
    await cb.answer(T.ADMIN_TICKET_CLOSED.format(id=tid))


# ───────────────── 👤 البحث + 💰 الرصيد ─────────────────

async def _show_user(target, user: dict) -> None:
    from app.bot import keyboards as K2
    username = f"@{user['username']}" if user.get("username") else "بدون @username"
    blocked = "موقوف" if user.get("is_blocked") else "غير موقوف"
    bot_blocked = "\n🚫 حظر البوت: نعم" if user.get("is_blocked_bot") else ""
    text = T.USER_CARD.format(name=T.esc(user.get("name")), username=T.esc(username), uid=user["tg_id"],
                              balance=fmt(user.get("balance_usd") or 0), orders=user.get("order_count", 0),
                              open_orders=user.get("open_orders", 0), tickets=user.get("open_tickets", 0),
                              topups=user.get("approved_topups", 0), blocked=blocked, bot_blocked=bot_blocked)
    await target.answer(text, reply_markup=K2.admin_user_card(user))


@router.callback_query(F.data == "adm:find")
async def cb_find(cb: CallbackQuery, state: FSMContext) -> None:
    await C.ask_input(cb, state, AdminTools.search, {}, T.USER_SEARCH_PROMPT, K.cancel_input("adm:cancel_input"))


@router.message(AdminTools.search, F.text)
async def msg_find(message: Message, state: FSMContext) -> None:
    if _cancelled_text(message):
        await state.clear()
        return
    user = await users_repo.admin_find(message.text)
    await state.clear()
    if not user:
        await message.answer(T.USER_NOT_FOUND, reply_markup=K.admin_back())
        return
    await _show_user(message, user)


@router.callback_query(F.data.regexp(r"^adm:user:(\d+):toggle$"))
async def cb_toggle_user(cb: CallbackQuery) -> None:
    uid = int(cb.data.split(":")[2])
    user = await users_repo.admin_find(str(uid))
    if not user:
        await cb.answer("غير موجود", show_alert=True)
        return
    new_value = not bool(user.get("is_blocked"))
    await users_repo.set_blocked(uid, new_value)
    await events.log_event("user_block_toggle", cb.from_user.id, payload_user=uid, blocked=new_value)
    user = await users_repo.admin_find(str(uid))
    await cb.message.edit_text(T.USER_CARD.format(
        name=T.esc(user.get("name")), username=T.esc(f"@{user['username']}" if user.get("username") else "بدون @username"),
        uid=uid, balance=fmt(user.get("balance_usd") or 0), orders=user.get("order_count", 0),
        open_orders=user.get("open_orders", 0), tickets=user.get("open_tickets", 0), topups=user.get("approved_topups", 0),
        blocked="موقوف" if user.get("is_blocked") else "غير موقوف", bot_blocked="\n🚫 حظر البوت: نعم" if user.get("is_blocked_bot") else ""),
        reply_markup=K.admin_user_card(user))
    await cb.answer("تم التحديث ✅")


@router.callback_query(F.data.regexp(r"^adm:user:(\d+):(add|sub)$"))
async def cb_adjust_start(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    uid, direction = int(parts[2]), parts[3]
    user = await users_repo.admin_find(str(uid))
    if not user:
        await cb.answer("المستخدم غير موجود", show_alert=True)
        return
    emoji = "➕" if direction == "add" else "➖"
    example = "5 تعويض تأخير" if direction == "add" else "3 تصحيح شحن مكرر"
    await C.ask_input(cb, state, AdminTools.balance_input,
                      {"uid": uid, "direction": direction, "before": str(user.get("balance_usd") or 0), "name": user.get("name") or ""},
                      T.USER_ADJUST_PROMPT.format(emoji=emoji, example=example), K.cancel_input("adm:cancel_input"))


@router.message(AdminTools.balance_input, F.text)
async def msg_adjust(message: Message, state: FSMContext) -> None:
    if _cancelled_text(message):
        await state.clear()
        return
    data = await state.get_data()
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(T.USER_ADJUST_INVALID)
        return
    from app.services import validators as V
    parsed = V.parse_usd(parts[0])   # v0.9.2: صارم — لا 1e3 ولا 5.555
    if parsed is None:
        await message.answer(T.USER_ADJUST_INVALID)
        return
    amount = money_value(parsed)
    reason = parts[1].strip()[:300]
    if amount <= 0:
        await message.answer(T.USER_ADJUST_INVALID)
        return
    if amount > Decimal("10000"):
        await message.answer(T.USER_ADJUST_TOO_LARGE)
        return
    before = Decimal(str(data.get("before") or 0))
    direction = data.get("direction")
    after = before + amount if direction == "add" else before - amount
    if after < 0:
        await message.answer(T.USER_BALANCE_BLOCKED.format(amount=fmt(amount), balance=fmt(before)))
        return
    import secrets
    nonce = secrets.token_hex(6)
    await state.update_data(amount=str(amount), reason=reason, after=str(after), nonce=nonce)
    verb = "إضافة" if direction == "add" else "خصم"
    direction_text = "الإضافة" if direction == "add" else "الخصم"
    text = T.USER_ADJUST_SUMMARY.format(name=T.esc(data.get("name")), uid=data["uid"], before=fmt(before),
                                        direction=direction_text, amount=fmt(amount), after=fmt(after), reason=T.esc(reason))
    await message.answer(text, reply_markup=K.admin_balance_confirm(int(data["uid"]), verb, fmt(amount), nonce))


@router.callback_query(F.data.regexp(r"^adm:bal:confirm:(\d+)(?::(\w+))?$"))
async def cb_adjust_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    uid = int(parts[3])
    nonce = parts[4] if len(parts) > 4 else ""
    data = await state.get_data()
    # الرمز يجب أن يطابق شاشة الملخص الحالية — زر قديم أو ضغطة بعد التنفيذ = جلسة منتهية
    if int(data.get("uid") or 0) != uid or not data.get("amount") or not nonce or nonce != data.get("nonce"):
        await cb.answer("انتهت جلسة التعديل — ابدأ من بطاقة المستخدم", show_alert=True)
        await state.clear()
        return
    amount = Decimal(str(data["amount"]))
    reason = str(data.get("reason") or "")[:300]
    direction = data.get("direction")
    try:
        if direction == "add":
            balance = await money.credit(uid, amount, "adjustment", ref_type="admin", note=reason, admin_id=cb.from_user.id,
                                         idem_key=f"adj-{nonce}")
            sign, word = "+", "إضافة"
        else:
            balance = await money.debit(uid, amount, "adjustment", ref_type="admin", note=reason, admin_id=cb.from_user.id,
                                        idem_key=f"adj-{nonce}")
            sign, word = "−", "خصم"
    except money.DuplicateOperation:
        # ضغطة مكررة/متزامنة: التعديل نُفّذ مرة واحدة فقط بالضغطة الأولى
        await cb.answer("✅ هذا التعديل نُفّذ مسبقاً — لم يتكرر", show_alert=False)
        return
    except money.InsufficientBalance as e:
        await state.clear()
        await cb.answer(T.USER_BALANCE_BLOCKED.format(amount=fmt(amount), balance=fmt(e.balance)), show_alert=True)
        return
    except Exception as e:  # noqa: BLE001
        await state.clear()
        await cb.answer(f"تعذّر التعديل: {str(e)[:120]}", show_alert=True)
        return
    await state.clear()
    await events.log_event("balance_adjustment", uid, admin_id=cb.from_user.id, amount=str(amount), direction=direction, reason=reason)
    try:
        await cb.bot.send_message(uid, T.USER_ADJUST_NOTICE.format(direction=word, amount=fmt(amount), balance=fmt(balance), reason=T.esc(reason)),
                                  reply_markup=K.balance_menu())
    except Exception:
        pass
    await cb.message.edit_text(T.USER_ADJUST_DONE.format(name=T.esc(data.get("name")), direction=word, amount=fmt(amount),
                                                         balance=fmt(balance), reason=T.esc(reason)), reply_markup=K.admin_back())
    await cb.answer("تم تسجيل الحركة ✅")


@router.callback_query(F.data.regexp(r"^adm:user:(\d+):(orders|tickets)$"))
async def cb_user_related(cb: CallbackQuery) -> None:
    uid, kind = int(cb.data.split(":")[2]), cb.data.split(":")[3]
    if kind == "orders":
        rows = await orders_repo.list_for_user(uid, limit=20)
        data = [(o["id"], f"{o['status']} #ORD-{o['id']} · {fmt(o['price_usd'])}") for o in rows]
        await cb.message.edit_text(f"📦 طلبات المستخدم <code>{uid}</code>", reply_markup=K.admin_orders_list(data) if data else K.admin_back())
    else:
        rows = await ticket_repo.list_for_user(uid)
        await cb.message.edit_text(f"🎫 تذاكر المستخدم <code>{uid}</code>", reply_markup=K.ticket_list(rows, admin=True) if rows else K.admin_back())
    await cb.answer()


# ───────────────── 📣 البث ─────────────────

@router.callback_query(F.data == "adm:bc")
async def cb_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    await C.ask_input(cb, state, AdminTools.broadcast_text, {}, T.BROADCAST_TEXT_PROMPT, K.cancel_input("adm:bc:cancel"))


@router.message(AdminTools.broadcast_text, F.text)
async def msg_broadcast_text(message: Message, state: FSMContext) -> None:
    if _cancelled_text(message):
        await state.clear()
        return
    text = message.text.strip()
    if not text:
        await message.answer(T.BROADCAST_TEXT_PROMPT)
        return
    if len(text) > 4000:
        await message.answer("النص طويل — الحد 4000 حرف.")
        return
    await state.update_data(text=text, photo_id=None)
    await state.set_state(AdminTools.broadcast_photo)
    await message.answer(T.BROADCAST_PHOTO_PROMPT, reply_markup=K.broadcast_photo())


@router.message(AdminTools.broadcast_photo, F.photo)
async def msg_broadcast_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(photo_id=message.photo[-1].file_id)
    await _broadcast_audience(message, state, data.get("text", ""), message.photo[-1].file_id)


async def _broadcast_audience(target, state: FSMContext, text: str, photo_id: str | None) -> None:
    counts = await users_repo.broadcast_counts()
    await state.set_state(AdminTools.broadcast_audience)
    await state.update_data(text=text, photo_id=photo_id, counts=counts)
    preview = T.BROADCAST_PREVIEW.format(body=T.esc(text).replace("{name}", "سامر"))
    if photo_id:
        await target.bot.send_photo(target.chat.id, photo_id, caption=preview, reply_markup=K.broadcast_audience(counts))
    else:
        await target.answer(preview, reply_markup=K.broadcast_audience(counts))


@router.callback_query(F.data == "adm:bc:no_photo")
async def cb_broadcast_no_photo(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if await state.get_state() != AdminTools.broadcast_photo.state:
        await cb.answer("انتهت جلسة البث", show_alert=True)
        return
    await _broadcast_audience(cb.message, state, data.get("text", ""), None)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:bc:aud:(\w+)$"))
async def cb_broadcast_audience(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("text"):
        await cb.answer("ابدأ البث من لوحة الأدمن", show_alert=True)
        return
    segment = cb.data.split(":")[-1]
    counts = data.get("counts") or await users_repo.broadcast_counts()
    count = int(counts.get(segment, 0))
    if not count:
        await cb.answer(T.BROADCAST_EMPTY, show_alert=True)
        return
    await state.set_state(AdminTools.broadcast_confirm)
    await state.update_data(segment=segment, count=count)
    labels = {"all": "الجميع", "balance": "لديهم رصيد", "ordered": "طلبوا سابقاً", "new": "لم يطلبوا بعد"}
    await cb.message.edit_text(T.BROADCAST_CONFIRM.format(segment=labels.get(segment, segment), count=count) +
                               (f"\n\n{T.BROADCAST_CONFIRM_BIG}" if count > 50 else ""),
                               reply_markup=K.broadcast_confirm(count, big=count > 50))
    await cb.answer()


# ───────── تنفيذ البث (v0.9.2) ─────────
# • يعمل في الخلفية: الضغطة تُجاب فوراً ولا يتعطل معالج البوت دقائق مع آلاف المستخدمين
# • بث واحد فقط في كل مرة: ضغطتا تأكيد متزامنتان لا تُرسلان الرسالة مرتين
# • النص يُهرَّب كما في المعاينة تماماً (<b> يظهر نصاً لا تنسيقاً — لا أخطاء HTML)
# • احترام حد تيليغرام: عند 429 ننتظر retry_after ونعيد المحاولة
BROADCAST_TASKS: set[asyncio.Task] = set()
_broadcast_busy = False
_BC_LABELS = {"all": "الجميع", "balance": "لديهم رصيد", "ordered": "طلبوا سابقاً", "new": "لم يطلبوا بعد"}


def render_broadcast(text: str, name: str | None) -> str:
    """نفس صيغة المعاينة: النص مهرَّب و{name} مهرَّب."""
    return T.esc(text).replace("{name}", T.esc(name or "صديقنا"))


async def _send_with_retry(bot, chat_id: int, body: str, photo_id: str | None, plain_len: int) -> None:
    for attempt in range(4):
        try:
            if photo_id:
                if plain_len <= 1024:
                    await bot.send_photo(chat_id, photo_id, caption=body)
                else:
                    await bot.send_photo(chat_id, photo_id)
                    await bot.send_message(chat_id, body)
            else:
                await bot.send_message(chat_id, body)
            return
        except TelegramRetryAfter as e:
            if attempt == 3:
                raise
            await asyncio.sleep(float(e.retry_after) + 0.5)


async def _broadcast_worker(bot, admin_id: int, segment: str, text: str, photo_id: str | None, recipients: list) -> None:
    global _broadcast_busy
    sent = blocked = failed = 0
    started = time.perf_counter()
    try:
        for target in recipients:
            name = str(target.get("name") or "صديقنا")
            body = render_broadcast(text, name)
            plain_len = len(text.replace("{name}", name))
            try:
                await _send_with_retry(bot, target["tg_id"], body, photo_id, plain_len)
                sent += 1
            except TelegramForbiddenError:
                blocked += 1
                await users_repo.mark_bot_blocked(target["tg_id"], True)
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "blocked" in msg or "chat not found" in msg or "deactivated" in msg:
                    blocked += 1
                    await users_repo.mark_bot_blocked(target["tg_id"], True)
                else:
                    failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
            await asyncio.sleep(0.05)   # ≈ 20 رسالة/ثانية — تحت حد تيليغرام (30)
        seconds = round(time.perf_counter() - started, 1)
        report = T.BROADCAST_PROGRESS.format(segment=_BC_LABELS.get(segment, segment), sent=sent, blocked=blocked,
                                             failed=failed, seconds=seconds)
        await events.log_event("broadcast_finished", admin_id, segment=segment, sent=sent, blocked=blocked, failed=failed)
        await bot.send_message(admin_id, report, reply_markup=K.admin_back())
    except Exception:  # noqa: BLE001
        log.exception("broadcast worker crashed")
        try:
            await bot.send_message(admin_id, f"⚠️ توقف البث بعد إرسال {sent} رسالة بسبب خطأ غير متوقع.",
                                   reply_markup=K.admin_back())
        except Exception:  # noqa: BLE001
            pass
    finally:
        _broadcast_busy = False


async def _run_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    global _broadcast_busy
    if _broadcast_busy:
        await cb.answer("⏳ يوجد بث قيد الإرسال الآن — انتظر حتى يكتمل", show_alert=True)
        return
    _broadcast_busy = True   # يُحجز قبل أي await ← الضغطة المتزامنة الثانية ترى البث مشغولاً
    try:
        data = await state.get_data()
        segment = data.get("segment")
        if not segment or not data.get("text"):
            await state.clear()
            await cb.answer("انتهت جلسة البث", show_alert=True)
            _broadcast_busy = False
            return
        await state.clear()
        recipients = await users_repo.broadcast_recipients(segment)
        if not recipients:
            await cb.answer(T.BROADCAST_EMPTY, show_alert=True)
            _broadcast_busy = False
            return
        task = asyncio.create_task(_broadcast_worker(cb.bot, cb.from_user.id, segment, str(data["text"]),
                                                     data.get("photo_id"), list(recipients)))
    except Exception:
        _broadcast_busy = False
        raise
    BROADCAST_TASKS.add(task)
    task.add_done_callback(BROADCAST_TASKS.discard)
    await cb.answer(f"🚀 بدأ البث إلى {len(recipients)} — سيصلك التقرير عند الانتهاء")
    try:
        await cb.message.edit_text(f"🚀 <b>جارٍ البث</b> إلى {len(recipients)} مستخدم…\nسيصلك تقرير عند الانتهاء.")
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data == "adm:bc:confirm")
async def cb_broadcast_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if int(data.get("count") or 0) > 50:
        await cb.answer(T.BROADCAST_CONFIRM_BIG, show_alert=True)
        return
    await _run_broadcast(cb, state)


@router.callback_query(F.data == "adm:bc:confirm2")
async def cb_broadcast_confirm2(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if int(data.get("count") or 0) <= 50:
        await cb.answer("اضغط التأكيد الأخضر", show_alert=True)
        return
    await _run_broadcast(cb, state)


@router.callback_query(F.data == "adm:bc:edit")
async def cb_broadcast_edit(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await C.ask_input(cb, state, AdminTools.broadcast_text, {"text": data.get("text", ""), "photo_id": data.get("photo_id")},
                      T.BROADCAST_TEXT_PROMPT, K.cancel_input("adm:bc:cancel"))


@router.callback_query(F.data == "adm:bc:cancel")
async def cb_broadcast_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer(T.BROADCAST_CANCELLED)
    try:
        await cb.message.edit_text(T.BROADCAST_CANCELLED, reply_markup=K.admin_back())
    except Exception:
        pass


@router.callback_query(F.data == "adm:cancel_input")
async def cb_cancel_input_local(cb: CallbackQuery, state: FSMContext) -> None:
    # admin/panel has the historical handler too; this one is registered earlier for all new states.
    await state.clear()
    await cb.answer("أُلغي ✅")
    try:
        await cb.message.edit_text("أُلغي ✅", reply_markup=K.admin_back())
    except Exception:
        pass
