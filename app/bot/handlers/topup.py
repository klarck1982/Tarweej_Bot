"""B0–B6 — الرصيد والشحن (خريطة الأزرار، الفرع B).

المسار: رصيدي ← شحن ← الشبكة ← المبلغ ← التعليمات (العنوان) ← «حوّلت» ← الإثبات ← بانتظار الاعتماد
ثم الأدمن يعتمد/يرفض (admin/topups.py) ← إشعار للعميل.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.config import settings as app_settings
from app.bot import texts as T
from app.db import pool as db
from app.db.repo import events, orders as orders_repo, settings as settings_repo, topups as topups_repo, users as users_repo
from app.services import payments as PM
from app.services.notify import notify_admins_topup
from app.services.pricing import fmt, money

router = Router(name="topup")
TZ = ZoneInfo(app_settings.tz)

TOPUP_MAX_DEFAULT = Decimal("1000")


async def _limits() -> tuple[Decimal, Decimal]:
    lo = Decimal(str(await settings_repo.get("min_topup_usd", 5)))
    hi = Decimal(str(await settings_repo.get("max_topup_usd", TOPUP_MAX_DEFAULT)))
    return lo, max(hi, lo)
LEDGER_KINDS = {"topup": "شحن", "order_charge": "طلب", "refund": "استرداد", "referral": "إحالة", "adjustment": "تعديل"}


class Topup(StatesGroup):
    amount = State()   # ينتظر رقماً مكتوباً
    proof = State()    # ينتظر صورة أو TxID


# ───────────── B0 رصيدي ─────────────

async def balance_view(uid: int) -> tuple[str, object]:
    balance = await users_repo.get_balance(uid)
    last = await db.fetchrow(
        "SELECT type, amount_usd, created_at FROM ledger WHERE user_id = $1 ORDER BY id DESC LIMIT 1", uid
    )
    if last:
        sign = "+" if last["amount_usd"] > 0 else "−"
        last_txt = f"آخر عملية: {LEDGER_KINDS.get(last['type'], last['type'])} {sign}{fmt(abs(last['amount_usd']))} — {last['created_at'].astimezone(TZ):%d/%m %H:%M}"
    else:
        last_txt = T.BALANCE_NO_TX
    text = T.BALANCE.format(balance=fmt(balance), last=last_txt)
    pending = await topups_repo.get_pending_for_user(uid)
    if pending:
        text += T.BALANCE_PENDING.format(id=pending["id"], amount=fmt(pending["amount_usd"]))
    draft = await orders_repo.get_awaiting(uid)
    if draft:
        gap = max(Decimal("0"), Decimal(draft["price_usd"]) - balance)
        text += f"\n\n📦 عندك طلب معلّق <b>#ORD-{draft['id']}</b> بقيمة {fmt(draft['price_usd'])}" + (f" — ينقصك <b>{fmt(gap)}</b>." if gap > 0 else " — رصيدك يكفي، أكمله الآن!")
    return text, K.balance_menu(pending["id"] if pending else None, draft["id"] if draft else None)


@router.message(Command("balance"))
@router.message(F.text == T.BTN_BALANCE)
async def m_balance(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await state.clear()
    text, kb = await balance_view(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "bal:menu")
async def cb_balance(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await balance_view(cb.from_user.id)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — قادم من رسالة بصورة أو قديمة
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


# ───────────── B1 الطريقة ─────────────

@router.callback_query(F.data == "bal:topup")
async def cb_topup(cb: CallbackQuery, state: FSMContext, keep_data: bool = False) -> None:
    if not keep_data:
        await state.clear()
    else:
        await state.set_state(None)  # نحتفظ بـ gap_usd لاقتراح مبلغ الشحن
    usable = await PM.usable_methods()
    if not usable:
        await cb.message.answer(T.TOPUP_NO_METHODS, reply_markup=K.home_only())
        await cb.answer()
        return
    await events.log_event("topup_start", cb.from_user.id)
    try:
        await cb.message.edit_text(T.TOPUP_METHOD, reply_markup=K.topup_methods(usable))
    except Exception:  # noqa: BLE001 — قادم من رسالة بلا نص قابل للتعديل
        await cb.message.answer(T.TOPUP_METHOD, reply_markup=K.topup_methods(usable))
    await cb.answer()


# ───────────── B2 المبلغ ─────────────

async def _amount_screen(uid: int, method_code: str, state: FSMContext) -> tuple[str, object]:
    methods = await PM.get_methods()
    m = methods.get(method_code)
    min_usd = Decimal(str(await settings_repo.get("min_topup_usd", 5)))
    presets = await settings_repo.get("topup_presets_usd", [5, 10, 20, 50, 100])
    text = T.TOPUP_AMOUNT.format(method=PM.label(m), min=fmt(min_usd))
    if PM.needs_rate(m):
        text += T.TOPUP_AMOUNT_SYP_NOTE.format(rate=PM.fmt_rate(await PM.syp_rate()))
    suggested = None
    data = await state.get_data()
    gap = data.get("gap_usd")
    if gap:
        suggested = float(max(Decimal(str(gap)), min_usd).quantize(Decimal("1")) + (1 if Decimal(str(gap)) % 1 else 0))
        text += T.TOPUP_AMOUNT_SUGGEST.format(gap=fmt(gap))
    _, max_usd = await _limits()
    return text, K.topup_amounts(presets, suggested, fmt(min_usd), fmt(max_usd))


@router.callback_query(F.data.startswith("bal:m:"))
async def cb_method(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data.split(":")[2]
    usable = await PM.usable_methods()
    if code not in usable:
        await cb.answer("هذه الطريقة غير متاحة الآن", show_alert=True)
        return
    await state.update_data(method=code)
    await state.set_state(Topup.amount)
    text, kb = await _amount_screen(cb.from_user.id, code, state)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


def instructions_for(row: dict, m: dict, sla: str) -> tuple[str, str]:
    """نص التعليمات + عنوان زر النسخ، حسب نوع الطريقة."""
    if m.get("kind") == "shamcash":
        usd_hint = f" (= {fmt(row['amount_usd'])})" if m.get("currency") == "SYP" else ""
        text = T.TOPUP_INSTRUCTIONS_SHAM.format(
            id=row["id"], currency_name="ليرة سورية" if m.get("currency") == "SYP" else "دولار",
            pay=PM.pay_amount(m, row["amount_usd"], row.get("amount_local")), usd_hint=usd_hint,
            address=m.get("address") or "—", holder=m.get("holder") or "—", sla=sla,
        )
        return text, "📋 نسخ رقم الحساب"
    text = T.TOPUP_INSTRUCTIONS.format(id=row["id"], amount=fmt(row["amount_usd"]), network=m.get("network", ""),
                                       address=m.get("address") or "—", sla=sla)
    return text, "📋 نسخ العنوان"


async def _create_and_show(message: Message, uid: int, amount: Decimal, state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    code = data.get("method")
    usable = await PM.usable_methods()
    m = usable.get(code)
    if not m:
        await message.answer(T.TOPUP_NO_METHODS, reply_markup=K.home_only())
        await state.clear()
        return
    amount_local = rate = None
    if PM.needs_rate(m):
        rate = await PM.syp_rate()
        amount_local = PM.syp_amount(amount, rate)
    row = await topups_repo.create(uid, code, amount, amount_local=amount_local, rate=rate)
    await state.clear()
    sla = await settings_repo.get("topup_sla_text", "")
    text, copy_label = instructions_for(dict(row), m, sla)
    kb = K.topup_instructions(row["id"], m["address"], copy_label)
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)
    await events.log_event("topup_created", uid, topup_id=row["id"], amount=str(amount), method=code,
                           amount_local=str(amount_local) if amount_local is not None else None)


def _parse_amount(raw: str) -> Decimal | None:
    raw = raw.strip().replace("$", "").replace("،", ".").replace(",", ".")
    # أرقام عربية → لاتينية
    raw = raw.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    try:
        return money(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None


@router.callback_query(Topup.amount, F.data == "bal:amt:type")
async def cb_amount_type(cb: CallbackQuery) -> None:
    lo, hi = await _limits()
    await cb.message.answer(T.TOPUP_AMOUNT_TYPE.format(min=fmt(lo), max=fmt(hi)), reply_markup=K.cancel_input("bal:cancel"))
    await cb.answer()


@router.callback_query(Topup.amount, F.data.startswith("bal:amt:"))
async def cb_amount(cb: CallbackQuery, state: FSMContext) -> None:
    amount = _parse_amount(cb.data.split(":")[2])
    min_usd, max_usd = await _limits()
    if amount is None or amount < min_usd or amount > max_usd:
        await cb.answer(T.TOPUP_AMOUNT_TOO_LOW.format(min=fmt(min_usd)).replace("<b>", "").replace("</b>", ""), show_alert=True)
        return
    await _create_and_show(cb.message, cb.from_user.id, amount, state, edit=True)
    await cb.answer()


@router.message(Topup.amount, F.text)
async def msg_amount(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        return  # تتركه للأوامر/القوائم (راوترات أخرى) — لا نبتلعه
    amount = _parse_amount(message.text)
    min_usd, max_usd = await _limits()
    if amount is None:
        await message.answer(T.TOPUP_AMOUNT_INVALID)
        return
    if amount < min_usd:
        await message.answer(T.TOPUP_AMOUNT_TOO_LOW.format(min=fmt(min_usd)))
        return
    if amount > max_usd:
        await message.answer(T.TOPUP_AMOUNT_TOO_HIGH.format(max=fmt(max_usd)))
        return
    await _create_and_show(message, message.from_user.id, amount, state, edit=False)


# ───────────── B3 عرض التعليمات مجدداً / B4 الإثبات ─────────────

@router.callback_query(F.data.startswith("bal:view:"))
async def cb_view(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row or row["user_id"] != cb.from_user.id:
        await cb.answer()
        return
    if row["status"] != "pending":
        await cb.answer("هذا الطلب حُسم بالفعل", show_alert=True)
        return
    await state.clear()
    methods = await PM.get_methods()
    m = methods.get(row["method"], {"kind": "crypto", "network": row["method"], "address": "—"})
    sla = await settings_repo.get("topup_sla_text", "")
    text, copy_label = instructions_for(dict(row), m, sla)
    kb = K.topup_instructions(tid, m.get("address") or "—", copy_label)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("bal:paid:"))
async def cb_paid(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    row = await topups_repo.get(tid)
    if not row or row["user_id"] != cb.from_user.id or row["status"] != "pending":
        await cb.answer("هذا الطلب غير متاح", show_alert=True)
        return
    await state.set_state(Topup.proof)
    await state.update_data(topup_id=tid)
    m = (await PM.get_methods()).get(row["method"], {})
    tmpl = T.TOPUP_PROOF_SHAM if m.get("kind") == "shamcash" else T.TOPUP_PROOF
    await cb.message.answer(tmpl.format(id=tid), reply_markup=K.topup_proof(tid))
    await cb.answer()


@router.message(Topup.proof, F.photo | F.document | F.text)
async def msg_proof(message: Message, state: FSMContext) -> None:
    if message.text and (message.text.startswith("/") or message.text in T.MAIN_BUTTONS):
        return
    data = await state.get_data()
    tid = data.get("topup_id")
    row = await topups_repo.get(tid) if tid else None
    if not row or row["status"] != "pending":
        await state.clear()
        await message.answer("هذا الطلب لم يعد معلّقاً.", reply_markup=K.home_only())
        return

    file_id = None
    tx = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id
    m = (await PM.get_methods()).get(row["method"], {})
    invalid = T.TOPUP_PROOF_INVALID_SHAM if m.get("kind") == "shamcash" else T.TOPUP_PROOF_INVALID
    if file_id is None and message.text:
        candidate = message.text.strip()
        if PM.proof_ok(m, candidate):
            tx = candidate
        else:
            await message.answer(invalid)
            return
    elif file_id is None:
        await message.answer(invalid)
        return

    await topups_repo.attach_proof(tid, file_id, tx)
    await state.clear()
    sla = await settings_repo.get("topup_sla_text", "")
    m_title = (await PM.get_methods()).get(row["method"], {}).get("title", row["method"])
    sent = await message.answer(
        T.TOPUP_WAITING.format(id=tid, amount=fmt(row["amount_usd"]), method=m_title, sla=sla),
        reply_markup=K.topup_waiting(tid),
    )
    await topups_repo.set_messages(tid, user_msg_id=sent.message_id)
    await events.log_event("topup_proof", message.from_user.id, topup_id=tid, has_image=bool(file_id), has_tx=bool(tx))
    # بطاقة للأدمن
    await notify_admins_topup(message.bot, tid)


# ───────────── إلغاء ─────────────

@router.callback_query(F.data == "bal:cancel")
async def cb_cancel_flow(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await balance_view(cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("تم الإلغاء")


@router.callback_query(F.data.startswith("bal:cancel:"))
async def cb_cancel_topup(cb: CallbackQuery, state: FSMContext) -> None:
    tid = int(cb.data.split(":")[2])
    ok = await topups_repo.cancel_by_user(tid, cb.from_user.id)
    await state.clear()
    if ok:
        await events.log_event("topup_cancelled", cb.from_user.id, topup_id=tid)
        await cb.message.edit_text(T.TOPUP_CANCELLED.format(id=tid), reply_markup=K.home_only())
        await cb.answer("تم الإلغاء")
    else:
        await cb.answer("هذا الطلب حُسم بالفعل", show_alert=True)


# ───────────── B6 السجل ─────────────

PAGE = 10


@router.callback_query(F.data.startswith("bal:hist:"))
async def cb_history(cb: CallbackQuery) -> None:
    page = max(1, int(cb.data.split(":")[2]))
    total = int(await db.fetchval("SELECT count(*) FROM ledger WHERE user_id = $1", cb.from_user.id) or 0)
    if not total:
        await cb.answer(T.HISTORY_EMPTY, show_alert=True)
        return
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = min(page, pages)
    rows = await db.fetch(
        "SELECT type, amount_usd, note, created_at FROM ledger WHERE user_id = $1 ORDER BY id DESC LIMIT $2 OFFSET $3",
        cb.from_user.id, PAGE, (page - 1) * PAGE,
    )
    lines = [T.HISTORY_TITLE.format(page=page, pages=pages)]
    for r in rows:
        sign = "+" if r["amount_usd"] > 0 else "−"
        lines.append(f"{sign}{fmt(abs(r['amount_usd']))}  {LEDGER_KINDS.get(r['type'], r['type'])} — {r['created_at'].astimezone(TZ):%d/%m %H:%M}")
    balance = await users_repo.get_balance(cb.from_user.id)
    lines.append(f"\n💰 الرصيد الحالي: <b>{fmt(balance)}</b>")
    await cb.message.edit_text("\n".join(lines), reply_markup=K.history_nav(page, pages))
    await cb.answer()


@router.callback_query(F.data == "nav:noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()
