"""A1 — مراجعة طلبات الشحن + A5 (جزء) — طرق الدفع والعناوين. للأدمن فقط."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.bot.handlers.admin import _common as C
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
    adjust_confirm = State()
    message_user = State()
    wallet_address = State()
    wallet_holder = State()
    wallet_confirm = State()
    rate_confirm = State()
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
    dest = cb.from_user.id if C.in_channel(cb) else cb.message.chat.id
    if row.get("proof_file_id"):
        await cb.bot.send_photo(dest, row["proof_file_id"], caption=text, reply_markup=kb)
    else:
        await cb.bot.send_message(dest, text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):kb$"))
async def cb_restore_kb(cb: CallbackQuery) -> None:
    """«رجوع» من قائمة أسباب الرفض: يعيد أزرار البطاقة في مكانها (يعمل في القناة والخاص)."""
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row:
        await cb.answer()
        return
    if row["status"] != "pending":
        await notify.refresh_admin_cards(cb.bot, tid)
        await cb.answer(T.ADMIN_ALREADY_DECIDED, show_alert=True)
        return
    remaining = max(0, await topups_repo.count_pending() - 1)
    kb = K.admin_topup_card(tid, has_proof_image=bool(row.get("proof_file_id")), remaining=remaining, in_channel=C.in_channel(cb))
    try:
        await cb.message.edit_reply_markup(reply_markup=kb)
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


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
        dest = cb.from_user.id if C.in_channel(cb) else cb.message.chat.id
        await cb.bot.send_photo(dest, row["proof_file_id"], caption=f"إثبات #TOP-{tid}")
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
    await C.ask_input(cb, state, AdminTopup.adjust_amount, {"tid": tid}, T.ADMIN_ADJUST_AMOUNT,
                      K.cancel_input("adm:cancel_input"))


@router.message(AdminTopup.adjust_amount, F.text)
async def msg_adjust(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    from app.services import validators as V
    parsed = V.parse_usd(message.text)
    try:
        if parsed is None:
            raise InvalidOperation
        amount = money(parsed)
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("اكتب رقماً صحيحاً بمنزلتين عشريتين كحد أقصى، مثل <code>9.5</code>")
        return
    data = await state.get_data()
    tid = data["tid"]
    # حد أعلى (v0.9.2): نفس حد الشحن في إعدادات Cpanel — خطأ إصبع (1000000) لا يصبح رصيداً حقيقياً
    limit = Decimal(str(await settings_repo.get("max_topup_usd", "1000")))
    if amount > limit:
        await message.answer(f"⛔ المبلغ أكبر من حد الشحن ({fmt(limit)}). اكتب مبلغاً أصغر، أو عدّل الحد من Cpanel.")
        return
    row = await topups_repo.get(tid)
    if not row or row["status"] != "pending":
        await state.clear()
        await message.answer(T.ADMIN_ALREADY_DECIDED)
        return
    requested = Decimal(str(row["amount_usd"]))
    if requested > 0 and abs(amount - requested) / requested > Decimal("0.5"):
        # فرق كبير عن طلب العميل ← تأكيد صريح
        await state.set_state(AdminTopup.adjust_confirm)
        await state.update_data(tid=tid, amount=str(amount))
        await message.answer(
            f"⚠️ <b>فرق كبير عن طلب العميل</b>\nطلب العميل: <b>{fmt(requested)}</b>\nالمبلغ الذي كتبته: <b>{fmt(amount)}</b>\n\n"
            f"هل تعتمد {fmt(amount)} بدل {fmt(requested)}؟",
            reply_markup=K.admin_topup_adjust_confirm(tid, fmt(amount)))
        return
    await state.clear()
    await _approve_adjusted(message, message.bot, message.from_user.id, tid, amount)


async def _approve_adjusted(target: Message, bot, admin_id: int, tid: int, amount: Decimal) -> None:
    ok, new_balance, row = await topups_repo.approve(tid, admin_id, amount_override=amount)
    if not ok:
        await target.answer(T.ADMIN_ALREADY_DECIDED)
        return
    await target.answer(f"✅ اعتُمد #TOP-{tid} بمبلغ {fmt(amount)} — رصيد العميل {fmt(new_balance)}")
    await _finish(target, bot, tid, ok, new_balance, row, adjusted=True)


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):adjc$"))
async def cb_adjust_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    data = await state.get_data()
    if await state.get_state() != AdminTopup.adjust_confirm.state or int(data.get("tid") or 0) != tid or not data.get("amount"):
        await cb.answer("انتهت الجلسة — افتح الطلب من جديد", show_alert=True)
        return
    amount = Decimal(data["amount"])
    await state.clear()   # قبل الاعتماد ← الضغطة الثانية ترى الجلسة منتهية
    await cb.answer()
    await _approve_adjusted(cb.message, cb.bot, cb.from_user.id, tid, amount)


# ───────────── رفض ─────────────

@router.callback_query(F.data.regexp(r"^adm:top:(\d+):no$"))
async def cb_reject_menu(cb: CallbackQuery) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row or row["status"] != "pending":
        await cb.answer(T.ADMIN_ALREADY_DECIDED, show_alert=True)
        return
    # نبدّل أزرار البطاقة نفسها بقائمة الأسباب (بلا رسالة جديدة — يعمل في القناة والخاص)
    try:
        await cb.message.edit_reply_markup(reply_markup=K.admin_reject_reasons(tid))
    except Exception:  # noqa: BLE001
        await cb.message.answer(T.ADMIN_REJECT_REASON.format(id=tid), reply_markup=K.admin_reject_reasons(tid))
    await cb.answer("اختر السبب 👇")


@router.callback_query(F.data.regexp(r"^adm:top:(\d+):no:(\w+)$"))
async def cb_reject_reason(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    tid, code = int(parts[2]), parts[4]
    if code == "custom":
        await C.ask_input(cb, state, AdminTopup.reject_reason, {"tid": tid}, T.ADMIN_REJECT_CUSTOM,
                          K.cancel_input("adm:cancel_input"))
        return
    reason = dict(T.REJECT_REASONS).get(code, code)
    row = await topups_repo.reject(tid, cb.from_user.id, reason)
    if not row:
        await cb.answer(T.ADMIN_ALREADY_DECIDED, show_alert=True)
        await notify.refresh_admin_cards(cb.bot, tid)
        return
    await cb.answer("تم الرفض ❌")
    await _finish(cb, cb.bot, tid, True, None, row)   # يحدّث البطاقة نفسها بالنتيجة ويزيل الأزرار


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
    await message.answer(f"❌ رُفض #TOP-{tid}: {notify.esc(message.text.strip())}")
    await _finish(message, message.bot, tid, True, None, row)


# ───────────── مراسلة العميل ─────────────

@router.callback_query(F.data.regexp(r"^adm:msg:(\d+)$"))
async def cb_message_user(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row:
        await cb.answer()
        return
    await C.ask_input(cb, state, AdminTopup.message_user, {"uid": row["user_id"], "tid": tid},
                      f"✍️ اكتب رسالتك للعميل {notify.esc(row['user_name'])} بخصوص #TOP-{tid}:",
                      K.cancel_input("adm:cancel_input"))


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


async def _render_wallet(cb: CallbackQuery, code: str) -> bool:
    """يعرض شاشة طريقة الدفع (بلا cb.answer). CallbackQuery مجمّد في aiogram 3 فلا نعدّل cb.data."""
    view = await _wallet_detail(code)
    if not view:
        return False
    try:
        await cb.message.edit_text(view[0], reply_markup=view[1])
    except Exception:  # noqa: BLE001
        await cb.message.answer(view[0], reply_markup=view[1])
    return True


@router.callback_query(F.data.regexp(r"^adm:wal:(\w+)$"))
async def cb_wallet(cb: CallbackQuery) -> None:
    await _render_wallet(cb, cb.data.split(":")[2])
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:wal:(\w+):toggle$"))
async def cb_wallet_toggle(cb: CallbackQuery) -> None:
    code = cb.data.split(":")[2]
    methods = await PM.get_methods()
    note = ""
    if code in methods:
        methods[code]["enabled"] = not methods[code].get("enabled", False)
        await PM.save_methods(methods)
        note = "تم التفعيل 🟢" if methods[code]["enabled"] else "تم الإيقاف 🔴"
    await _render_wallet(cb, code)
    await cb.answer(note)   # إجابة واحدة فقط


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
        await message.answer(PM.address_hint(m))
        return
    # v0.9.2: العنوان/رقم الحساب يُعرض للمراجعة قبل الحفظ — حرف واحد خطأ = أموال العملاء تضيع
    await state.set_state(AdminTopup.wallet_confirm)
    await state.update_data(code=code, pending_address=addr)
    what = "رقم الحساب" if m.get("kind") == "shamcash" else "العنوان"
    await message.answer(
        f"🔍 <b>راجع {what} قبل الحفظ</b>\n{notify.esc(m['title'])}\n\n<code>{notify.esc(addr)}</code>\n\n"
        f"البداية: <b>{notify.esc(addr[:6])}</b> · النهاية: <b>{notify.esc(addr[-6:])}</b>\n"
        f"قارنهما مع حسابك حرفاً بحرف. كل التحويلات القادمة ستصل إلى {what} هذا.",
        reply_markup=K.admin_wallet_confirm(code))


@router.callback_query(F.data.regexp(r"^adm:wal:(\w+):save$"))
async def cb_wallet_save(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data.split(":")[2]
    data = await state.get_data()
    if await state.get_state() != AdminTopup.wallet_confirm.state or data.get("code") != code or not data.get("pending_address"):
        await cb.answer("انتهت الجلسة — أدخل العنوان من جديد", show_alert=True)
        return
    await cb.answer("✅ حُفظ")
    await _save_wallet_address(cb.message, state, code, data["pending_address"], admin_id=cb.from_user.id)


async def _save_wallet_address(message: Message, state: FSMContext, code: str, addr: str, admin_id: int | None = None) -> None:
    methods = await PM.get_methods()
    m = methods.get(code)
    if not m:
        await state.clear()
        return
    await state.clear()
    methods[code]["address"] = addr
    await PM.save_methods(methods)
    await events.log_event("wallet_updated", admin_id or message.from_user.id, method=code)
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
    current = await PM.syp_rate()
    if current > 0 and abs(rate - current) / current > Decimal("0.3"):
        # v0.9.2: تغيّر أكثر من 30% ← غالباً صفر ناقص/زائد؛ كل دفعات الليرة تُحسب منه
        await state.set_state(AdminTopup.rate_confirm)
        await state.update_data(pending_rate=str(rate))
        await message.answer(
            f"⚠️ <b>تغيّر كبير في سعر الصرف</b>\nالحالي: 1$ = <b>{PM.fmt_rate(current)}</b> ل.س\n"
            f"الجديد: 1$ = <b>{PM.fmt_rate(rate)}</b> ل.س\n\nتأكد من عدد الأصفار — كل مبالغ شام كاش ليرة تُحسب من هذا السعر.",
            reply_markup=K.admin_rate_confirm())
        return
    await _save_rate(message, state, rate, message.from_user.id)


async def _save_rate(message: Message, state: FSMContext, rate: Decimal, admin_id: int) -> None:
    await state.clear()
    await PM.set_syp_rate(rate)
    await events.log_event("syp_rate_updated", admin_id, rate=str(rate))
    await message.answer(T.ADMIN_RATE_SAVED.format(rate=PM.fmt_rate(rate)))
    text, kb = await _wallets_view()
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "adm:rate:save")
async def cb_rate_save(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if await state.get_state() != AdminTopup.rate_confirm.state or not data.get("pending_rate"):
        await cb.answer("انتهت الجلسة — أدخل السعر من جديد", show_alert=True)
        return
    await cb.answer("✅ حُفظ")
    await _save_rate(cb.message, state, Decimal(data["pending_rate"]), cb.from_user.id)
