"""A1 — مراجعة طلبات الشحن + A5 (جزء) — طرق الدفع والعناوين. للأدمن فقط."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.config import settings
from app.db.repo import events, settings as settings_repo, topups as topups_repo
from app.services import notify
from app.services import payments as PM
from app.services.pricing import fmt, money

router = Router(name="admin_topups")
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))


class AdminTopup(StatesGroup):
    reject_reason = State()
    adjust_amount = State()
    message_user = State()
    wallet_address = State()
    wallet_holder = State()
    syp_rate = State()


# ───────────── القائمة ─────────────

async def _list_view() -> tuple[str, object]:
    rows = await topups_repo.list_pending()
    if not rows:
        return T.ADMIN_TOPUP_LIST_EMPTY, K.admin_back()
    data = []
    methods = await PM.get_methods()
    for r in rows:
        net = methods.get(r["method"], {}).get("short", r["method"])
        proof = "📷" if r["proof_file_id"] else ("🔖" if r["proof_text"] else "⏳")
        data.append((r["id"], f"{proof} #TOP-{r['id']} · {fmt(r['amount_usd'])} {net} · {r['user_name'][:18]}"))
    return T.ADMIN_TOPUP_LIST.format(n=len(rows)), K.admin_topup_list(data)


@router.callback_query(F.data == "adm:topups")
async def cb_list(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _list_view()
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — رسالة بصورة (بطاقة) لا تُحرَّر كنص
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "adm:topups:next")
async def cb_next(cb: CallbackQuery) -> None:
    rows = await topups_repo.list_pending(limit=1)
    if not rows:
        await cb.answer(T.ADMIN_TOPUP_LIST_EMPTY, show_alert=True)
        return
    await _send_card(cb, rows[0]["id"])
    await cb.answer()


async def _send_card(cb: CallbackQuery, tid: int) -> None:
    text, row = await notify.topup_card_text(tid)
    if not row:
        await cb.answer("غير موجود", show_alert=True)
        return
    kb = None
    if row["status"] == "pending":
        remaining = max(0, await topups_repo.count_pending() - 1)
        kb = K.admin_topup_card(tid, has_proof_image=bool(row.get("proof_file_id")), remaining=remaining)
    else:
        kb = K.admin_back()
    if row.get("proof_file_id"):
        await cb.message.answer_photo(row["proof_file_id"], caption=text, reply_markup=kb)
    else:
        await cb.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):view$"))
async def cb_view(cb: CallbackQuery) -> None:
    tid = int(cb.data.split(":")[2])
    await _send_card(cb, tid)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):proof$"))
async def cb_proof(cb: CallbackQuery) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if row and row["proof_file_id"]:
        await cb.message.answer_photo(row["proof_file_id"], caption=f"إثبات #TOP-{tid}")
    await cb.answer()


# ───────────── اعتماد ─────────────

async def _finish(cb_or_msg, bot, tid: int, ok: bool, new_balance, row: dict | None, adjusted: bool = False) -> None:
    if not ok or not row:
        return
    await notify.refresh_admin_cards(bot, tid)
    await notify.notify_user_topup_result(bot, row, new_balance, adjusted=adjusted)
    await events.log_event("topup_" + row["status"], row["user_id"], topup_id=tid, amount=str(row["amount_usd"]),
                           admin_id=row.get("admin_id"))


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):ok$"))
async def cb_approve(cb: CallbackQuery) -> None:
    tid = int(cb.data.split(":")[2])
    ok, new_balance, row = await topups_repo.approve(tid, cb.from_user.id)
    if not ok:
        await cb.answer(T.ADMIN_ALREADY_DECIDED, show_alert=True)
        await notify.refresh_admin_cards(cb.bot, tid)
        return
    await cb.answer(f"✅ اعتُمد — رصيد العميل {fmt(new_balance)}")
    await _finish(cb, cb.bot, tid, ok, new_balance, row)


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):adj$"))
async def cb_adjust(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row or row["status"] != "pending":
        await cb.answer(T.ADMIN_ALREADY_DECIDED, show_alert=True)
        return
    await state.set_state(AdminTopup.adjust_amount)
    await state.update_data(tid=tid)
    await cb.message.answer(T.ADMIN_ADJUST_AMOUNT, reply_markup=K.cancel_input(f"adm:top:{tid}:view"))
    await cb.answer()


@router.message(AdminTopup.adjust_amount, F.text)
async def msg_adjust(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    raw = message.text.strip().replace("$", "").replace(",", ".").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    try:
        amount = money(Decimal(raw))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("اكتب رقماً صحيحاً مثل <code>9.5</code>")
        return
    data = await state.get_data()
    tid = data["tid"]
    await state.clear()
    ok, new_balance, row = await topups_repo.approve(tid, message.from_user.id, amount_override=amount)
    if not ok:
        await message.answer(T.ADMIN_ALREADY_DECIDED)
        return
    await message.answer(f"✅ اعتُمد #TOP-{tid} بمبلغ {fmt(amount)} — رصيد العميل {fmt(new_balance)}")
    await _finish(message, message.bot, tid, ok, new_balance, row, adjusted=True)


# ───────────── رفض ─────────────

@router.callback_query(F.data.regexp(r"^adm:top:(\d+):no$"))
async def cb_reject_menu(cb: CallbackQuery) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row or row["status"] != "pending":
        await cb.answer(T.ADMIN_ALREADY_DECIDED, show_alert=True)
        return
    await cb.message.answer(T.ADMIN_REJECT_REASON.format(id=tid), reply_markup=K.admin_reject_reasons(tid))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):no:(\w+)$"))
async def cb_reject_reason(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    tid, code = int(parts[2]), parts[4]
    if code == "custom":
        await state.set_state(AdminTopup.reject_reason)
        await state.update_data(tid=tid)
        await cb.message.edit_text(T.ADMIN_REJECT_CUSTOM, reply_markup=K.cancel_input(f"adm:top:{tid}:view"))
        await cb.answer()
        return
    reason = dict(T.REJECT_REASONS).get(code, code)
    row = await topups_repo.reject(tid, cb.from_user.id, reason)
    if not row:
        await cb.answer(T.ADMIN_ALREADY_DECIDED, show_alert=True)
        return
    await cb.message.edit_text(f"❌ رُفض #TOP-{tid}: {reason}", reply_markup=K.admin_back())
    await cb.answer("تم الرفض")
    await _finish(cb, cb.bot, tid, True, None, row)


@router.message(AdminTopup.reject_reason, F.text)
async def msg_reject_reason(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    tid = data["tid"]
    await state.clear()
    row = await topups_repo.reject(tid, message.from_user.id, message.text.strip())
    if not row:
        await message.answer(T.ADMIN_ALREADY_DECIDED)
        return
    await message.answer(f"❌ رُفض #TOP-{tid}: {notify.esc(message.text.strip())}", reply_markup=K.admin_back())
    await _finish(message, message.bot, tid, True, None, row)


# ───────────── مراسلة العميل ─────────────

@router.callback_query(F.data.regexp(r"^adm:msg:(\d+)$"))
async def cb_message_user(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row:
        await cb.answer()
        return
    await state.set_state(AdminTopup.message_user)
    await state.update_data(uid=row["user_id"], tid=tid)
    await cb.message.answer(f"✍️ اكتب رسالتك للعميل {notify.esc(row['user_name'])} بخصوص #TOP-{tid}:",
                            reply_markup=K.cancel_input(f"adm:top:{tid}:view"))
    await cb.answer()


@router.message(AdminTopup.message_user, F.text)
async def msg_message_user(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    try:
        await message.bot.send_message(
            data["uid"], f"💬 <b>رسالة من الدعم بخصوص طلب الشحن #TOP-{data['tid']}:</b>\n{notify.esc(message.text)}",
            reply_markup=K.support_menu(),
        )
        await message.answer("✅ أُرسلت.")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"تعذّر الإرسال: {e}")


# ───────────── A5: طرق الدفع والحسابات + سعر الصرف ─────────────

def _addr_html(m: dict) -> str:
    if not m.get("address"):
        return "<i>لم يُدخل بعد</i>"
    extra = f" — {notify.esc(m['holder'])}" if m.get("kind") == "shamcash" and m.get("holder") else ""
    return f"<code>{notify.esc(m['address'])}</code>{extra}"


async def _wallets_view() -> tuple[str, object]:
    methods = await PM.get_methods()
    rate = await PM.syp_rate()
    rows = [f"• <b>{notify.esc(m['title'])}</b> — {PM.status_text(m, rate)}\n  {_addr_html(m)}" for m in methods.values()]
    rate_txt = f"1$ = {PM.fmt_rate(rate)} ل.س" if rate > 0 else "<i>غير مضبوط — طريقة الليرة مخفية</i>"
    return T.ADMIN_WALLETS.format(rows="\n".join(rows), rate=rate_txt), K.admin_wallets(methods, rate)


@router.callback_query(F.data == "adm:wallets")
async def cb_wallets(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _wallets_view()
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


async def _wallet_detail(code: str) -> tuple[str, object] | None:
    methods = await PM.get_methods()
    m = methods.get(code)
    if not m:
        return None
    rate = await PM.syp_rate()
    lines = [f"🏦 <b>{notify.esc(m['title'])}</b>"]
    if m.get("kind") == "shamcash":
        lines.append(f"رقم الحساب: {'<code>' + notify.esc(m['address']) + '</code>' if m.get('address') else '<i>لم يُدخل بعد</i>'}")
        lines.append(f"اسم صاحب الحساب: {notify.esc(m['holder']) if m.get('holder') else '<i>لم يُدخل بعد</i>'}")
        if m.get("currency") == "SYP":
            lines.append(f"سعر الصرف: {('1$ = ' + PM.fmt_rate(rate) + ' ل.س') if rate > 0 else '<i>غير مضبوط</i>'}")
    else:
        lines.append(f"الشبكة: {m.get('network', '')}")
        lines.append(f"العنوان الحالي: {'<code>' + notify.esc(m['address']) + '</code>' if m.get('address') else '<i>لم يُدخل بعد</i>'}")
    lines.append(f"الحالة: {PM.status_text(m, rate)}")
    return "\n".join(lines), K.admin_wallet_edit(code, m)


@router.callback_query(F.data.regexp(r"^adm:wal:(\w+)$"))
async def cb_wallet(cb: CallbackQuery) -> None:
    code = cb.data.split(":")[2]
    view = await _wallet_detail(code)
    if not view:
        await cb.answer()
        return
    try:
        await cb.message.edit_text(view[0], reply_markup=view[1])
    except Exception:  # noqa: BLE001
        await cb.message.answer(view[0], reply_markup=view[1])
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:wal:(\w+):toggle$"))
async def cb_wallet_toggle(cb: CallbackQuery) -> None:
    code = cb.data.split(":")[2]
    methods = await PM.get_methods()
    if code in methods:
        methods[code]["enabled"] = not methods[code].get("enabled", False)
        await PM.save_methods(methods)
        await cb.answer("تم التفعيل 🟢" if methods[code]["enabled"] else "تم الإيقاف 🔴")
    cb.data = f"adm:wal:{code}"
    await cb_wallet(cb)


@router.callback_query(F.data.regexp(r"^adm:wal:(\w+):(edit|holder)$"))
async def cb_wallet_edit(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, code, what = cb.data.split(":")
    methods = await PM.get_methods()
    m = methods.get(code)
    if not m:
        await cb.answer()
        return
    await state.update_data(code=code)
    title = notify.esc(m["title"])
    if what == "holder":
        await state.set_state(AdminTopup.wallet_holder)
        prompt = T.ADMIN_WALLET_HOLDER
    else:
        await state.set_state(AdminTopup.wallet_address)
        prompt = (T.ADMIN_WALLET_EDIT_SHAM if m.get("kind") == "shamcash" else T.ADMIN_WALLET_EDIT).format(title=title)
    await cb.message.answer(prompt, reply_markup=K.cancel_input(f"adm:wal:{code}"))
    await cb.answer()


async def _after_wallet_save(message: Message, code: str) -> None:
    view = await _wallet_detail(code)
    if view:
        await message.answer(view[0], reply_markup=view[1])


@router.message(AdminTopup.wallet_address, F.text)
async def msg_wallet_address(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    code = data.get("code")
    methods = await PM.get_methods()
    m = methods.get(code)
    if not m:
        await state.clear()
        return
    addr = PM.clean_address(m, message.text)
    if not addr:
        hint = "رقم الحساب مو واضح — أرسله أرقاماً/أحرفاً بلا مسافات." if m.get("kind") == "shamcash" \
            else "العنوان مو واضح — انسخه كاملاً من محفظتك بلا مسافات."
        await message.answer(hint)
        return
    await state.clear()
    methods[code]["address"] = addr
    await PM.save_methods(methods)
    await events.log_event("wallet_updated", message.from_user.id, method=code)
    await message.answer(T.ADMIN_WALLET_SAVED.format(title=notify.esc(m["title"]), address=notify.esc(addr)))
    if m.get("kind") == "shamcash" and not m.get("holder"):
        # نكمل مباشرة باسم صاحب الحساب — خطوة واحدة أقل على الأدمن
        await state.set_state(AdminTopup.wallet_holder)
        await state.update_data(code=code)
        await message.answer(T.ADMIN_WALLET_HOLDER, reply_markup=K.cancel_input(f"adm:wal:{code}"))
        return
    await _after_wallet_save(message, code)


@router.message(AdminTopup.wallet_holder, F.text)
async def msg_wallet_holder(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    holder = " ".join(message.text.split())
    if not (2 <= len(holder) <= 60):
        await message.answer("اكتب الاسم كما يظهر في شام كاش (2–60 حرفاً).")
        return
    data = await state.get_data()
    code = data.get("code")
    await state.clear()
    methods = await PM.get_methods()
    if code not in methods:
        return
    methods[code]["holder"] = holder
    await PM.save_methods(methods)
    await events.log_event("wallet_holder_updated", message.from_user.id, method=code)
    await message.answer(f"✅ اسم صاحب الحساب: <b>{notify.esc(holder)}</b>")
    await _after_wallet_save(message, code)


# ───────── سعر صرف الليرة ─────────

@router.callback_query(F.data == "adm:rate")
async def cb_rate(cb: CallbackQuery, state: FSMContext) -> None:
    rate = await PM.syp_rate()
    await state.set_state(AdminTopup.syp_rate)
    await cb.message.answer(
        T.ADMIN_RATE_EDIT.format(rate=(PM.fmt_rate(rate) + " ل.س") if rate > 0 else "غير مضبوط"),
        reply_markup=K.cancel_input("adm:wallets"),
    )
    await cb.answer()


@router.message(AdminTopup.syp_rate, F.text)
async def msg_rate(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    rate = PM.parse_rate(message.text)
    if rate is None:
        await message.answer(T.ADMIN_RATE_INVALID)
        return
    await state.clear()
    await PM.set_syp_rate(rate)
    await events.log_event("syp_rate_updated", message.from_user.id, rate=str(rate))
    await message.answer(T.ADMIN_RATE_SAVED.format(rate=PM.fmt_rate(rate)))
    text, kb = await _wallets_view()
    await message.answer(text, reply_markup=kb)
