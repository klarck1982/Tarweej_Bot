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
from app.services import nour, order_notify as ON, orders as orders_svc
from app.services import validators as V
from app.services.pricing import fmt

router = Router(name="admin_orders")
router.message.filter(F.from_user.id.in_(set(settings.admin_ids)))
router.callback_query.filter(F.from_user.id.in_(set(settings.admin_ids)))


class AdminOrder(StatesGroup):
    refund_reason = State()
    message_user = State()
    fallback_username = State()
    tga_revision = State()
    tga_reject = State()
    tga_results = State()
    tga_text = State()
    tgp_when = State()
    tgp_url = State()
    tgp_views = State()
    tgp_reject = State()
    tgp_text = State()


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
    kb = ON.admin_card_kb(o, media_count, False)
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
                      T.ADMIN_ORDER_REFUND_CONFIRM.format(price=fmt(o["price_usd"] - o.get("refunded_usd", 0)), id=oid)
                      + (T.ADMIN_ORDER_REFUND_NOUR_WARN.format(nour_id=o["nour_id"])
                         if o.get("nour_id") and o.get("kind") == "meta_campaign" else ""),
                      K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.refund_reason, F.text)
async def msg_refund(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    try:
        o = await orders_svc.refund(data["oid"], reason=message.text.strip(), new_status="refunded",
                                    admin_id=message.from_user.id, expect=repo.OPEN_STATUSES)
    except orders_svc.RefundBusy:
        await message.answer(T.ADMIN_ORDER_REFUND_BUSY.format(id=data["oid"]))
        return
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
    woken = await repo.wake_waiting_username()
    await message.answer(T.ADMIN_FALLBACK_SAVED.format(username=u)
                         + (T.ADMIN_FALLBACK_WOKEN.format(n=woken) if woken else ""), reply_markup=K.admin_settings_menu())


# ───────────── 📣 Telegram Ads: انتقالات يدوية ─────────────

@router.callback_query(F.data.regexp(r"^adm:tga:(\d+):to:(\w+)$"))
async def cb_tga_to(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    oid, to = int(parts[2]), parts[4]
    o = await repo.get(oid)
    if not o or o.get("kind") != "tg_ads":
        await cb.answer()
        return
    if to not in orders_svc.MANUAL_TRANSITIONS.get(o["status"], ()):
        await cb.answer("هذا الانتقال غير متاح من الحالة الحالية", show_alert=True)
        await ON.refresh_admin_cards(cb.bot, oid)
        return
    if to == "needs_revision":
        await C.ask_input(cb, state, AdminOrder.tga_revision, {"oid": oid}, T.ADMIN_TGA_ASK_REVISION, K.cancel_input("adm:cancel_input"))
        return
    if to == "rejected":
        await C.ask_input(cb, state, AdminOrder.tga_reject, {"oid": oid}, T.ADMIN_TGA_ASK_REJECT.format(id=oid), K.cancel_input("adm:cancel_input"))
        return
    if to == "completed":
        await C.ask_input(cb, state, AdminOrder.tga_results, {"oid": oid}, T.ADMIN_TGA_ASK_RESULTS.format(id=oid), K.cancel_input("adm:cancel_input"))
        return
    o2, changed = await orders_svc.manual_transition(oid, to, cb.from_user.id)
    await cb.answer(f"✅ {orders_svc.STATUS_NAME.get(to, to)}" + (" — أُبلغ العميل" if changed else ""))
    if changed and o2:
        await ON.push_user_status(cb.bot, o2)
    await ON.refresh_admin_cards(cb.bot, oid)


async def _tga_finish(message: Message, state: FSMContext, to: str, note: str | None = None, results: dict | None = None) -> None:
    data = await state.get_data()
    await state.clear()
    oid = data["oid"]
    o, changed = await orders_svc.manual_transition(oid, to, message.from_user.id, note=note, results=results)
    if not changed:
        await message.answer("لم يُنفَّذ — الطلب تغيّرت حالته.")
        return
    await message.answer(f"✅ #ORD-{oid} → {orders_svc.STATUS_NAME.get(to, to)} — أُبلغ العميل.")
    await ON.push_user_status(message.bot, o, reason=note)
    await ON.refresh_admin_cards(message.bot, oid)


@router.message(AdminOrder.tga_revision, F.text)
async def msg_tga_revision(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    await _tga_finish(message, state, "needs_revision", note=message.text.strip())


@router.message(AdminOrder.tga_reject, F.text)
async def msg_tga_reject(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    await _tga_finish(message, state, "rejected", note=message.text.strip())


@router.message(AdminOrder.tga_results, F.text)
async def msg_tga_results(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    nums = [V.parse_int(x) for x in message.text.replace(",", " ").replace("،", " ").split()]
    nums = [n for n in nums if n is not None]
    if len(nums) < 2 or any(n < 0 for n in nums[:2]):
        await message.answer(T.ADMIN_TGA_RESULTS_INVALID)
        return
    await _tga_finish(message, state, "completed", results={"views": nums[0], "clicks": nums[1]})


@router.callback_query(F.data.regexp(r"^adm:tga:(\d+):text$"))
async def cb_tga_text(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    o = await repo.get(oid)
    if not o or o.get("kind") != "tg_ads" or o["status"] in repo.FINAL_STATUSES:
        await cb.answer()
        return
    await C.ask_input(cb, state, AdminOrder.tga_text, {"oid": oid}, T.ADMIN_TGA_ASK_TEXT.format(id=oid), K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.tga_text, F.text)
async def msg_tga_text(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    txt = " ".join(message.text.split())
    if not 10 <= len(txt) <= 160:
        await message.answer(T.TGA_TEXT_TOO_LONG.format(n=len(txt)) if len(txt) > 160 else T.TGA_TEXT_TOO_SHORT)
        return
    data = await state.get_data()
    await state.clear()
    o = await repo.get(data["oid"])
    if not o:
        return
    spec = {**o["spec"], "text": txt, "text_by_team": True}
    o = await repo.update(o["id"], spec=spec)
    await events.log_event("tga_text_by_team", message.from_user.id, o["id"])
    await message.answer(f"✅ حُفظ النص لطلب #ORD-{o['id']} ({len(txt)} حرفاً) — أُبلغ العميل.")
    try:
        await message.bot.send_message(o["user_id"], T.TGA_TEXT_BY_TEAM.format(id=o["id"], text=T.esc(txt)),
                                       reply_markup=K.order_view({**o, "media_count": 0}))
    except Exception:  # noqa: BLE001
        pass
    await ON.refresh_admin_cards(message.bot, o["id"])


# ───────────── 📝 القنوات الشريكة: بطاقة طلب النشر ─────────────

import re  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app.services import partner_posts as PP  # noqa: E402

_POST_URL_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:c/\d+|[A-Za-z][A-Za-z0-9_]{3,31})/\d+(?:\?.*)?$", re.I)


async def _tgp_order(cb: CallbackQuery, oid: int, statuses: tuple[str, ...]) -> dict | None:
    o = await repo.get(oid)
    if not o or o.get("kind") != "tg_post":
        await cb.answer()
        return None
    if o["status"] not in statuses:
        await cb.answer("هذا الإجراء غير متاح من الحالة الحالية", show_alert=True)
        await ON.refresh_admin_cards(cb.bot, oid)
        return None
    return o


@router.callback_query(F.data.regexp(r"^adm:tgp:(\d+):when$"))
async def cb_tgp_when(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    if await _tgp_order(cb, oid, ("submitted", "in_progress")):
        await C.ask_input(cb, state, AdminOrder.tgp_when, {"oid": oid}, T.ADMIN_TGP_ASK_WHEN.format(id=oid, tz=settings.tz),
                          K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.tgp_when, F.text)
async def msg_tgp_when(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    when = PP.parse_when(message.text, ZoneInfo(settings.tz))
    if not when:
        await message.answer(T.ADMIN_TGP_WHEN_INVALID)
        return
    data = await state.get_data()
    await state.clear()
    o = await PP.schedule(data["oid"], message.from_user.id, when)
    if not o:
        await message.answer("لم يُنفَّذ — الطلب تغيّرت حالته.")
        return
    await message.answer(f"📅 #ORD-{o['id']} مجدول: <b>{ON._when(when)}</b> — أُبلغ العميل.")
    await ON.push_user_status(message.bot, o)
    await ON.refresh_admin_cards(message.bot, o["id"])


@router.callback_query(F.data.regexp(r"^adm:tgp:(\d+):url$"))
async def cb_tgp_url(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    if await _tgp_order(cb, oid, ("submitted", "in_progress")):
        await C.ask_input(cb, state, AdminOrder.tgp_url, {"oid": oid}, T.ADMIN_TGP_ASK_URL.format(id=oid), K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.tgp_url, F.text)
async def msg_tgp_url(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    raw = message.text.strip()
    if not _POST_URL_RE.match(raw):
        await message.answer(T.ADMIN_TGP_URL_INVALID)
        return
    url = raw if raw.startswith("http") else "https://" + raw
    data = await state.get_data()
    await state.clear()
    o = await PP.publish(data["oid"], message.from_user.id, url)
    if not o:
        await message.answer("لم يُنفَّذ — الطلب تغيّرت حالته.")
        return
    await message.answer(f"🟢 #ORD-{o['id']} منشور — ينتهي تلقائياً {ON._when(o['ends_at'])}. أُبلغ العميل بالرابط.")
    await ON.push_user_status(message.bot, o)
    await ON.refresh_admin_cards(message.bot, o["id"])


@router.callback_query(F.data.regexp(r"^adm:tgp:(\d+):views$"))
async def cb_tgp_views(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    if await _tgp_order(cb, oid, ("active", "completed")):
        await C.ask_input(cb, state, AdminOrder.tgp_views, {"oid": oid}, T.ADMIN_TGP_ASK_VIEWS.format(id=oid), K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.tgp_views, F.text)
async def msg_tgp_views(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    n = V.parse_int(message.text.replace(",", "").replace("،", "").strip())
    if n is None or n < 0:
        await message.answer("اكتب رقماً صحيحاً — مثال <code>8400</code>")
        return
    data = await state.get_data()
    await state.clear()
    o = await PP.set_views(data["oid"], message.from_user.id, n)
    if not o:
        await message.answer("لم يُنفَّذ — الطلب تغيّرت حالته.")
        return
    await message.answer(f"👁️ حُفظت المشاهدات لـ #ORD-{o['id']}: <b>{n:,}</b>" + (" — تصل العميل مع إشعار الانتهاء." if o["status"] == "active" else "."))
    await ON.refresh_admin_cards(message.bot, o["id"])


@router.callback_query(F.data.regexp(r"^adm:tgp:(\d+):finish$"))
async def cb_tgp_finish(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    if not await _tgp_order(cb, oid, ("active",)):
        return
    o = await PP.finish(oid, cb.from_user.id)
    if not o:
        await cb.answer("لم يُنفَّذ — الطلب تغيّرت حالته.", show_alert=True)
        return
    await cb.answer("✅ انتهى — أُبلغ العميل")
    await ON.push_user_status(cb.bot, o)
    await ON.refresh_admin_cards(cb.bot, oid)


@router.callback_query(F.data.regexp(r"^adm:tgp:(\d+):reject$"))
async def cb_tgp_reject(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    if await _tgp_order(cb, oid, ("submitted", "in_progress", "active")):
        await C.ask_input(cb, state, AdminOrder.tgp_reject, {"oid": oid}, T.ADMIN_TGP_ASK_REJECT.format(id=oid), K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.tgp_reject, F.text)
async def msg_tgp_reject(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    reason = message.text.strip()[:300]
    o = await PP.reject(data["oid"], message.from_user.id, reason)
    if not o:
        await message.answer("لم يُنفَّذ — الطلب تغيّرت حالته.")
        return
    await message.answer(f"❌ #ORD-{o['id']} — استُرد {fmt(o['refunded_usd'])} للعميل وأُبلغ.")
    await ON.push_user_status(message.bot, o, reason=reason)
    await ON.refresh_admin_cards(message.bot, o["id"])
    if o.get("owner_user_id") and o.get("owner_deadline"):
        from app.bot import mp_texts as TX
        from app.services import mp_notify
        await mp_notify.owner_note(message.bot, o, TX.NOTE_ADMIN_REJECT.format(id=o["id"], reason=T.esc(reason)))


@router.callback_query(F.data.regexp(r"^adm:tgp:(\d+):text$"))
async def cb_tgp_text(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    if await _tgp_order(cb, oid, ("submitted", "in_progress")):
        await C.ask_input(cb, state, AdminOrder.tgp_text, {"oid": oid},
                          f"✍️ اكتب نص المنشور الذي صغته للعميل #ORD-{oid} (حتى 1000 حرف) — سيُحفظ في الطلب ويصل العميل للاطلاع:",
                          K.cancel_input("adm:cancel_input"))


@router.message(AdminOrder.tgp_text, F.text)
async def msg_tgp_text(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    txt = message.text.strip()
    if not 5 <= len(txt) <= 1000:
        await message.answer(T.TGP_TEXT_TOO_LONG.format(n=len(txt)) if len(txt) > 1000 else "النص قصير جداً.")
        return
    data = await state.get_data()
    await state.clear()
    o = await repo.get(data["oid"])
    if not o:
        return
    o = await repo.update(o["id"], spec={**o["spec"], "text": txt, "text_by_team": True})
    await events.log_event("tgp_text_by_team", message.from_user.id, o["id"])
    await message.answer(f"✅ حُفظ النص لطلب #ORD-{o['id']} ({len(txt)} حرفاً) — أُبلغ العميل.")
    try:
        await message.bot.send_message(o["user_id"], f"✍️ <b>#ORD-{o['id']}: جهّز فريقنا نص منشورك:</b>\n<code>{T.esc(txt)}</code>\n\nسيُنشر به في موعده. إذا أردت تعديلاً بسيطاً راسلنا من «مساعدة بهذا الطلب» قبل النشر.",
                                       reply_markup=K.tgp_order_view({**o, "media_count": 0}))
    except Exception:  # noqa: BLE001
        pass
    await ON.refresh_admin_cards(message.bot, o["id"])
    from app.services import mp_notify
    await mp_notify.after_customer_paid(message.bot, o["id"])   # 💼 قناة سوق: النص جاهز ← طلب القبول لصاحبها


# ═══════════════════════════ 🎨 مهام التصميم (v0.8.0) ═══════════════════════════
# ▶️ بدأت العمل → 📤 تسليم (ملفات + تعليق ثم «أرسل للعميل») → العميل يعتمد/يطلب تعديلاً → ✅
# ❌ تعذّر التنفيذ = استرداد كامل بسبب يصل العميل. كل الإدخالات في خاصّ الأدمن (C.ask_input).

from app.services import design as DS  # noqa: E402


class AdminDesign(StatesGroup):
    deliver = State()       # يجمع ملفات التسليم + تعليقاً
    reject = State()        # سبب تعذّر التنفيذ


async def _ds_order(cb: CallbackQuery, oid: int, statuses: tuple[str, ...]) -> dict | None:
    o = await repo.get(oid)
    if not o or o.get("kind") != "design":
        await cb.answer()
        return None
    if o["status"] not in statuses:
        await cb.answer("هذا الإجراء غير متاح من الحالة الحالية", show_alert=True)
        await ON.refresh_admin_cards(cb.bot, oid)
        return None
    return o


@router.callback_query(F.data.regexp(r"^adm:ds:(\d+):start$"))
async def cb_ds_start(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    if not await _ds_order(cb, oid, ("submitted",)):
        return
    o = await DS.start(oid, cb.from_user.id)
    if not o:
        await cb.answer("تغيّرت حالة الطلب", show_alert=True)
        return
    await cb.answer(T.ADMIN_DS_STARTED.format(id=oid))
    await ON.push_user_status(cb.bot, o)
    await ON.refresh_admin_cards(cb.bot, oid)


@router.callback_query(F.data.regexp(r"^adm:ds:(\d+):deliver$"))
async def cb_ds_deliver(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    if await _ds_order(cb, oid, DS.WORKING):
        await C.ask_input(cb, state, AdminDesign.deliver, {"oid": oid, "files": [], "text": None},
                          T.ADMIN_DS_ASK_DELIVER.format(id=oid), K.admin_deliver_step(oid, False))


def _file_from(message: Message) -> tuple[str, str] | None:
    if message.document:
        return ("document", message.document.file_id)
    if message.photo:
        return ("photo", message.photo[-1].file_id)
    if message.video:
        return ("video", message.video.file_id)
    if message.animation:
        return ("video", message.animation.file_id)
    if message.audio:
        return ("audio", message.audio.file_id)
    if message.voice:
        return ("audio", message.voice.file_id)
    return None


@router.message(AdminDesign.deliver, F.document | F.photo | F.video | F.animation | F.audio | F.voice)
async def msg_ds_deliver_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    files = list(data.get("files") or [])
    f = _file_from(message)
    if not f:
        return
    if len(files) >= 10:
        await message.answer("الحد 10 ملفات لكل تسليم — اضغط «✅ أرسل للعميل».")
        return
    files.append(list(f))
    text = data.get("text")
    if message.caption and not text:
        text = " ".join(message.caption.split())[:1000]
    await state.update_data(files=files, text=text)
    if message.media_group_id and data.get("last_group") == message.media_group_id:
        return
    await state.update_data(last_group=message.media_group_id)
    await message.answer(T.ADMIN_DS_DELIVER_GOT.format(n=len(files), text=f" + تعليق «{T.esc(text[:40])}…»" if text else ""),
                         reply_markup=K.admin_deliver_step(data["oid"], True))


@router.message(AdminDesign.deliver, F.text)
async def msg_ds_deliver_text(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    text = " ".join(message.text.split())[:1000]
    await state.update_data(text=text)
    n = len(data.get("files") or [])
    await message.answer(T.ADMIN_DS_DELIVER_GOT.format(n=n, text=f" + تعليق «{T.esc(text[:40])}…»") if n else
                         "📝 حُفظ التعليق — " + T.ADMIN_DS_DELIVER_EMPTY, reply_markup=K.admin_deliver_step(data["oid"], n > 0))


@router.callback_query(AdminDesign.deliver, F.data.regexp(r"^adm:ds:(\d+):send$"))
async def cb_ds_send(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    data = await state.get_data()
    files = data.get("files") or []
    if int(data.get("oid") or 0) != oid:
        await cb.answer("انتهت الجلسة — ابدأ التسليم من بطاقة الطلب", show_alert=True)
        await state.clear()
        return
    if not files:
        await cb.answer(T.ADMIN_DS_DELIVER_EMPTY, show_alert=True)
        return
    await state.clear()
    o = await DS.deliver(oid, cb.from_user.id, files, data.get("text"))
    if not o:
        await cb.answer("لم يُنفَّذ — الطلب تغيّرت حالته.", show_alert=True)
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    ok = await ON.push_delivery(cb.bot, o)
    msg = T.ADMIN_DS_DELIVERED_OK.format(id=oid, n=len(files), hours=DS.approve_hours())
    if not ok:
        msg += "\n⚠️ تعذّر إيصال الرسالة للعميل (ربما حظر البوت) — التسليم محفوظ في الطلب."
    await cb.message.answer(msg)
    await cb.answer("📤 سُلّم")
    await ON.refresh_admin_cards(cb.bot, oid)


@router.callback_query(F.data.regexp(r"^adm:ds:(\d+):send$"))
async def cb_ds_send_stale(cb: CallbackQuery) -> None:
    await cb.answer("انتهت جلسة التسليم — اضغط 📤 تسليم من بطاقة الطلب من جديد", show_alert=True)


@router.callback_query(F.data.regexp(r"^adm:ds:(\d+):approve$"))
async def cb_ds_approve(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    if not await _ds_order(cb, oid, ("delivered",)):
        return
    o = await DS.approve(oid, None, admin_id=cb.from_user.id)
    if not o:
        await cb.answer("تغيّرت حالة الطلب", show_alert=True)
        return
    await cb.answer("✅ اعتُمد")
    try:
        await cb.bot.send_message(o["user_id"], T.DS_APPROVED.format(id=oid), reply_markup=K.ds_order_view({**o, "media_count": 0}))
    except Exception:  # noqa: BLE001
        pass
    await ON.refresh_admin_cards(cb.bot, oid)


@router.callback_query(F.data.regexp(r"^adm:ds:(\d+):files$"))
async def cb_ds_files(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await repo.get(oid)
    if not o or o.get("kind") != "design":
        await cb.answer()
        return
    dest = cb.from_user.id if C.in_channel(cb) else cb.message.chat.id
    n = 0
    for d in (o.get("delivery") or []):
        n += await ON.send_delivery_files(cb.bot, dest, o, d)
    await cb.answer(f"📥 {n} ملف" + (" — في خاصّك" if C.in_channel(cb) else "") if n else "لا تسليمات بعد")


@router.callback_query(F.data.regexp(r"^adm:ds:(\d+):reject$"))
async def cb_ds_reject(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    if await _ds_order(cb, oid, DS.OPEN):
        await C.ask_input(cb, state, AdminDesign.reject, {"oid": oid}, T.ADMIN_DS_ASK_REJECT.format(id=oid), K.cancel_input("adm:cancel_input"))


@router.message(AdminDesign.reject, F.text)
async def msg_ds_reject(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/") or message.text in T.MAIN_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    reason = message.text.strip()[:300]
    o = await DS.reject(data["oid"], message.from_user.id, reason)
    if not o:
        await message.answer("لم يُنفَّذ — الطلب تغيّرت حالته.")
        return
    await message.answer(f"❌ #ORD-{o['id']} — استُرد {fmt(o['refunded_usd'])} للعميل وأُبلغ.")
    await ON.push_user_status(message.bot, o, reason=reason)
    await ON.refresh_admin_cards(message.bot, o["id"])
    if o.get("owner_user_id") and o.get("owner_deadline"):
        from app.bot import mp_texts as TX
        from app.services import mp_notify
        await mp_notify.owner_note(message.bot, o, TX.NOTE_ADMIN_REJECT.format(id=o["id"], reason=T.esc(reason)))
