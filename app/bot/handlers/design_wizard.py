"""🎨 خدمات الكتابة والتصميم — معالج الطلب D0–D9 (التنفيذ يدوي بيد الأدمن من بطاقة المهمة).

D0 الخدمة/الباقة ← D1 النشاط ← D2 الرسالة ← D3 المواد (إن لزم) ← D4 الهوية البصرية ← D5 اللغة والنبرة
← D6 إضافات الفيديو (ريل/مونتاج فقط) ← D7 ملاحظات ← D8 الملخص والتأكيد (الخصم هنا فقط) ← D9 تم.
بعد التسليم: العميل «✅ اعتمده» أو «✏️ طلب تعديل» (الأول مجاني ثم مدفوع بتأكيد) — والاعتماد تلقائي بعد مهلة الصمت.
رصيد ناقص → مسودة awaiting_payment + «اشحن الفرق» (نفس آلية Meta/القنوات؛ الاستئناف من ord:resume).
"""

from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as K
from app.bot import texts as T
from app.db.repo import events, orders as orders_repo, settings as settings_repo, users as users_repo
from app.services import cpanel as CP, design as DS, orders as orders_svc, pricing as P
from app.services.money import InsufficientBalance
from app.services.pricing import fmt, money

router = Router(name="design_wizard")


class Design(StatesGroup):
    choosing = State()      # شاشات الأزرار
    message = State()       # ينتظر الرسالة الأساسية
    media = State()         # ينتظر صوراً/فيديو
    brand = State()         # ينتظر لوغو/ألوان
    notes = State()         # ينتظر ملاحظات
    revision = State()      # ينتظر نص طلب التعديل (بعد التسليم)


# ───────────── أدوات ─────────────

async def _edit(cb: CallbackQuery, text: str, kb) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        try:
            await cb.message.answer(text, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass


async def _send(target, text: str, kb, new_message: bool) -> None:
    if isinstance(target, CallbackQuery):
        if new_message:
            await target.message.answer(text, reply_markup=kb)
        else:
            await _edit(target, text, kb)
    else:
        await target.answer(text, reply_markup=kb)


def _skip_text(message: Message) -> bool:
    return bool(message.text) and (message.text.startswith("/") or message.text in T.MAIN_BUTTONS)


def _steps(d: dict) -> list[str]:
    """ترتيب الشاشات حسب الخدمة — لعدّاد «الخطوة n من N» وللرجوع."""
    items = d.get("items") or []
    steps = ["biz", "msg"]
    if DS.needs_media(items):
        steps.append("media")
    steps.append("brand")
    steps.append("lang")
    if DS.has_video(items):
        steps.append("extras")
    steps += ["notes", "summary"]
    return steps


def _step_line(d: dict, key: str) -> str:
    steps = _steps(d)
    return T.DS_STEP.format(n=steps.index(key) + 1, total=len(steps)) if key in steps else ""


def _prev(d: dict, key: str) -> str:
    steps = _steps(d)
    i = steps.index(key) if key in steps else 0
    return "ds:back:" + (steps[i - 1] if i > 0 else "svc")


async def _ensure(state: FSMContext) -> dict | None:
    d = await state.get_data()
    return d if d.get("kind") == "design" and d.get("items") else None


async def _enabled() -> bool:
    svc = await settings_repo.services()
    return bool(svc.get("addons", True))


# ───────────── D0 الخدمة / الباقة ─────────────

async def _begin(cb: CallbackQuery, state: FSMContext, items: list[str], bundle_idx: int | None, intro: str) -> None:
    await state.clear()
    await state.update_data(kind="design", items=items, bundle_idx=bundle_idx, media=[], extras=[], brand={},
                            lang="levant", tone="fun")
    await events.log_event("design_start", cb.from_user.id, items=items, bundle=bundle_idx)
    d = await state.get_data()
    await state.set_state(Design.choosing)
    await _edit(cb, intro + T.DS_BUSINESS + "\n" + _step_line(d, "biz"), K.ds_business(DS.BUSINESS, "nav:design"))


@router.callback_query(F.data.regexp(r"^add:svc:(copy|design|reel|montage)$"))
async def cb_service(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _enabled():
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    if CP.maintenance_text():
        await cb.answer(CP.maintenance_text(), show_alert=True)
        return
    code = cb.data.split(":")[-1]
    a = P.ADDONS[code]
    await events.log_event("addon_click", cb.from_user.id, svc=code)
    intro = T.DS_SVC_INTRO[code].format(price=fmt(a["price"]), hours=a["hours"])
    await _begin(cb, state, [code], None, intro)
    await cb.answer()


def _bundle_row(b: dict) -> str:
    save = money(Decimal(b["was"]) - Decimal(b["price"]))
    if save > 0:
        return T.DS_BUNDLE_ROW.format(title=T.esc(b["title"]), price=fmt(b["price"]), was=fmt(b["was"]), save=fmt(save))
    return T.DS_BUNDLE_ROW_PLAIN.format(title=T.esc(b["title"]), price=fmt(b["price"]))


def _bundle_was(b: dict) -> str:
    return f" <s>{fmt(b['was'])}</s>" if Decimal(b["was"]) > Decimal(b["price"]) else ""


def _bundle_rows() -> list[tuple[int, str]]:
    out = []
    for i, b in enumerate(P.ADDON_BUNDLES):
        out.append((i, f"📦 {b['title']} — {fmt(b['price'])}"))
    return out


@router.callback_query(F.data == "add:svc:bundles")
async def cb_bundles(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _enabled():
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    await state.clear()
    rows = "\n".join(_bundle_row(b) for b in P.ADDON_BUNDLES)
    await _edit(cb, T.DS_BUNDLES.format(rows=rows), K.ds_bundles(_bundle_rows()))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^ds:bundle:(\d+)$"))
async def cb_bundle(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _enabled():
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    if CP.maintenance_text():
        await cb.answer(CP.maintenance_text(), show_alert=True)
        return
    i = int(cb.data.split(":")[2])
    if not 0 <= i < len(P.ADDON_BUNDLES):
        await cb.answer("الباقة غير متاحة", show_alert=True)
        return
    b = P.ADDON_BUNDLES[i]
    items = list(b["items"])
    intro = T.DS_BUNDLE_INTRO.format(title=T.esc(b["title"]), price=fmt(b["price"]), was=_bundle_was(b),
                                     items=DS.items_title(items), hours=DS.deliver_hours(items))
    await _begin(cb, state, items, i, intro)
    await cb.answer()


# ───────────── D1 النشاط ─────────────

async def _show_biz(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.choosing)
    await _send(target, T.DS_BUSINESS.strip() + "\n" + _step_line(d, "biz"), K.ds_business(DS.BUSINESS, "nav:design"), new_message)


@router.callback_query(Design.choosing, F.data.regexp(r"^ds:biz:(\w+)$"))
async def cb_biz(cb: CallbackQuery, state: FSMContext) -> None:
    d = await _ensure(state)
    if not d:
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    code = cb.data.split(":")[2]
    if code not in dict(DS.BUSINESS):
        await cb.answer()
        return
    await state.update_data(business=code)
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_msg(cb, state)
    await cb.answer()


# ───────────── D2 الرسالة ─────────────

async def _show_msg(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.message)
    await _send(target, T.DS_MESSAGE + "\n" + _step_line(d, "msg"), K.ds_message_step(_prev(d, "msg")), new_message)


@router.message(Design.message, F.text)
async def msg_message(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    txt = " ".join(message.text.split())
    if len(txt) < 10:
        await message.answer(T.DS_MESSAGE_SHORT)
        return
    if len(txt) > DS.MAX_MESSAGE:
        await message.answer(T.DS_MESSAGE_LONG.format(n=len(txt)))
        return
    await state.update_data(message=txt)
    d = await state.get_data()
    if d.get("editing"):
        await _show_summary(message, state, new_message=True)
    elif DS.needs_media(d["items"]):
        await _show_media(message, state, new_message=True)
    else:
        await _show_brand(message, state, new_message=True)


# ───────────── D3 المواد ─────────────

def _media_counts(d: dict) -> tuple[int, int]:
    kinds = [m[0] for m in (d.get("media") or [])]
    return kinds.count("photo"), kinds.count("video") + kinds.count("document")


def _media_req(items) -> str:
    for c in ("reel", "montage", "design"):
        if c in items:
            return T.DS_MEDIA_REQ[c]
    return ""


async def _show_media(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.media)
    ph, vd = _media_counts(d)
    n = ph + vd
    enough = DS.media_ok(d["items"], ph, vd)
    if n:
        text = T.DS_MEDIA_GOT.format(photos=_n(ph, "صورة", "صور"), videos=_n(vd, "فيديو", "فيديو"))
    else:
        text = T.DS_MEDIA.format(req=_media_req(d["items"]), max=DS.MAX_MEDIA)
    await _send(target, text + "\n" + _step_line(d, "media"), K.ds_media(n, enough, _prev(d, "media")), new_message)


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 or n > 10 else many}" if n != 0 else f"0 {one}"


@router.message(Design.media, F.photo | F.video | F.document | F.animation)
async def msg_media(message: Message, state: FSMContext) -> None:
    d = await state.get_data()
    media = list(d.get("media") or [])
    if len(media) >= DS.MAX_MEDIA:
        await message.answer(T.DS_MEDIA_MAX.format(max=DS.MAX_MEDIA))
        return
    if message.photo:
        media.append(["photo", message.photo[-1].file_id])
    elif message.video:
        media.append(["video", message.video.file_id])
    elif message.animation:
        media.append(["video", message.animation.file_id])
    elif message.document and (message.document.mime_type or "").startswith(("image/", "video/")):
        kind = "photo" if (message.document.mime_type or "").startswith("image/") else "video"
        media.append([kind if kind == "photo" else "document", message.document.file_id])
    else:
        await message.answer(T.DS_MEDIA_TYPE)
        return
    await state.update_data(media=media, no_media=False)
    # ألبوم = عدة رسائل متتالية بنفس media_group_id — نرد مرة واحدة على آخرها (تقريبياً: نرد دائماً لكن بتحرير خفيف)
    if message.media_group_id and d.get("last_group") == message.media_group_id:
        return
    await state.update_data(last_group=message.media_group_id)
    await _show_media(message, state, new_message=True)


@router.message(Design.media, F.text)
async def msg_media_text(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    await _show_media(message, state, new_message=True)


@router.callback_query(Design.media, F.data == "ds:media:done")
async def cb_media_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    ph, vd = _media_counts(d)
    if not DS.media_ok(d["items"], ph, vd) and not d.get("media_warned"):
        await state.update_data(media_warned=True)
        await cb.answer(T.DS_MEDIA_NOT_ENOUGH.format(req=_media_req(d["items"]).replace("<b>", "").replace("</b>", "")), show_alert=True)
        return
    await state.update_data(no_media=False)
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_brand(cb, state)
    await cb.answer()


@router.callback_query(Design.media, F.data == "ds:media:none")
async def cb_media_none(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    await state.update_data(media=[], no_media=True)
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_brand(cb, state)
    await cb.answer("👍 نستخدم صوراً جاهزة")


@router.callback_query(Design.media, F.data == "ds:media:clear")
async def cb_media_clear(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(media=[], media_warned=False)
    await _show_media(cb, state)
    await cb.answer("🗑️ مُسح")


# ───────────── D4 الهوية البصرية ─────────────

def _brand_label(b: dict) -> str:
    parts = []
    if b.get("logo"):
        parts.append("لوغو")
    if b.get("colors"):
        parts.append(f"«{T.esc(b['colors'])}»")
    return " + ".join(parts)


async def _show_brand(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.brand)
    uid = target.from_user.id
    saved = await DS.brand_kit(uid)
    b = d.get("brand") or {}
    got = bool(b.get("logo") or b.get("colors"))
    if got:
        text = T.DS_BRAND_GOT.format(what=_brand_label(b))
    else:
        text = T.DS_BRAND.format(saved=T.DS_BRAND_SAVED.format(what=_brand_label(saved)) if saved else "")
    await _send(target, text + "\n" + _step_line(d, "brand"), K.ds_brand(bool(saved), got, _prev(d, "brand")), new_message)


@router.message(Design.brand, F.photo | F.document)
async def msg_brand_logo(message: Message, state: FSMContext) -> None:
    d = await state.get_data()
    b = dict(d.get("brand") or {})
    if message.photo:
        b["logo"] = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        b["logo"] = message.document.file_id
    else:
        await message.answer("أرسل اللوغو كصورة أو ملف PNG/JPG 🙂")
        return
    caption = " ".join((message.caption or "").split())
    if caption and not b.get("colors"):
        b["colors"] = caption[:120]
    b["saved"] = False
    await state.update_data(brand=b)
    await _show_brand(message, state, new_message=True)


@router.message(Design.brand, F.text)
async def msg_brand_colors(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    d = await state.get_data()
    b = dict(d.get("brand") or {})
    b["colors"] = " ".join(message.text.split())[:120]
    b["saved"] = False
    await state.update_data(brand=b)
    await _show_brand(message, state, new_message=True)


async def _after_brand(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_lang(cb, state)


@router.callback_query(Design.brand, F.data == "ds:brand:done")
async def cb_brand_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    b = d.get("brand") or {}
    if b.get("logo") or b.get("colors"):
        await DS.save_brand_kit(cb.from_user.id, b.get("logo"), b.get("colors"))
    await _after_brand(cb, state)
    await cb.answer("💾 حُفظت هويتك للطلبات القادمة")


@router.callback_query(Design.brand, F.data == "ds:brand:saved")
async def cb_brand_saved(cb: CallbackQuery, state: FSMContext) -> None:
    saved = await DS.brand_kit(cb.from_user.id)
    if not saved:
        await cb.answer("ما في هوية محفوظة بعد", show_alert=True)
        return
    await state.update_data(brand={"logo": saved.get("logo"), "colors": saved.get("colors"), "saved": True})
    await _after_brand(cb, state)
    await cb.answer()


@router.callback_query(Design.brand, F.data == "ds:brand:skip")
async def cb_brand_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(brand={})
    await _after_brand(cb, state)
    await cb.answer()


# ───────────── D5 اللغة والنبرة ─────────────

async def _show_lang(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.choosing)
    await _send(target, T.DS_LANG + "\n" + _step_line(d, "lang"),
                K.ds_lang(DS.LANGS, DS.TONES, d.get("lang", "levant"), d.get("tone", "fun"), _prev(d, "lang")), new_message)


@router.callback_query(Design.choosing, F.data.regexp(r"^ds:(lang|tone):(\w+)$"))
async def cb_lang(cb: CallbackQuery, state: FSMContext) -> None:
    d = await _ensure(state)
    if not d:
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    _, what, code = cb.data.split(":")
    if what == "lang" and code == "done":
        if d.get("editing"):
            await _show_summary(cb, state)
        elif DS.has_video(d["items"]):
            await _show_extras(cb, state)
        else:
            await _show_notes(cb, state)
        await cb.answer()
        return
    table = DS.LANGS if what == "lang" else DS.TONES
    if code not in dict(table):
        await cb.answer()
        return
    await state.update_data(**{what: code})
    await _show_lang(cb, state)
    await cb.answer()


# ───────────── D6 إضافات الفيديو ─────────────

async def _show_extras(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.choosing)
    price = DS.quote_from_state(d)
    base = DS.base_price({"items": d["items"], "bundle_idx": d.get("bundle_idx")})
    detail = f" ({fmt(base)} + تعليق صوتي {fmt(P.ADDON_VOICEOVER)})" if "voiceover" in (d.get("extras") or []) else ""
    await _send(target, T.DS_EXTRAS.format(price=fmt(price), detail=detail) + "\n" + _step_line(d, "extras"),
                K.ds_extras(DS.EXTRAS, d.get("extras") or [], fmt(P.ADDON_VOICEOVER), _prev(d, "extras")), new_message)


@router.callback_query(Design.choosing, F.data.regexp(r"^ds:extra:(\w+)$"))
async def cb_extra(cb: CallbackQuery, state: FSMContext) -> None:
    d = await _ensure(state)
    if not d:
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    code = cb.data.split(":")[2]
    if code not in {c for c, _, _ in DS.EXTRAS}:
        await cb.answer()
        return
    extras = list(d.get("extras") or [])
    if code in extras:
        extras.remove(code)
    else:
        extras.append(code)
    await state.update_data(extras=extras)
    await _show_extras(cb, state)
    await cb.answer()


@router.callback_query(Design.choosing, F.data == "ds:extras:done")
async def cb_extras_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await _ensure(state)
    if not d:
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_notes(cb, state)
    await cb.answer()


# ───────────── D7 ملاحظات ─────────────

async def _show_notes(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.notes)
    await _send(target, T.DS_NOTES + "\n" + _step_line(d, "notes"), K.ds_notes(_prev(d, "notes")), new_message)


@router.message(Design.notes, F.text)
async def msg_notes(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    txt = " ".join(message.text.split())
    if len(txt) > DS.MAX_NOTES:
        await message.answer(T.DS_NOTES_LONG.format(n=len(txt)))
        return
    await state.update_data(notes=txt)
    await _show_summary(message, state, new_message=True)


@router.callback_query(Design.notes, F.data == "ds:notes:skip")
async def cb_notes_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(notes=None)
    await _show_summary(cb, state)
    await cb.answer()


# ───────────── D8 الملخص ─────────────

def summary_text(spec: dict, price: Decimal, balance: Decimal) -> tuple[str, bool, Decimal]:
    ok = balance >= price
    gap = money(price - balance) if not ok else Decimal("0")
    footer = T.DS_SUMMARY_FOOTER_OK if ok else T.META_SUMMARY_GAP.format(gap=fmt(gap))
    text = T.DS_SUMMARY.format(lines="\n".join(DS.spec_lines(spec, T.esc)), hours=spec["deliver_hours"],
                               price=fmt(price), balance=fmt(balance), footer=footer)
    return text, ok, gap


async def _show_summary(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(Design.choosing)
    await state.update_data(editing=False)
    if not d.get("message"):
        await _show_msg(target, state, new_message)
        return
    spec = DS.build_spec(d)
    _, price, cost = orders_svc.compute_prices(spec)
    uid = target.from_user.id
    balance = await users_repo.get_balance(uid)
    text, ok, gap = summary_text(spec, price, balance)
    kb = K.ds_summary(ok, fmt(price), fmt(gap) if not ok else None)
    if not ok:
        days_valid = int(await settings_repo.get("order_draft_days", 7))
        draft = await orders_repo.save_awaiting(uid, {**spec, "media": d.get("media") or []}, price, cost, days_valid,
                                                d.get("draft_id"), kind="design")
        await state.update_data(draft_id=draft["id"], gap_usd=str(gap))
    await _send(target, text, kb, new_message)


@router.callback_query(Design.choosing, F.data == "ds:edit")
async def cb_edit(cb: CallbackQuery, state: FSMContext) -> None:
    d = await _ensure(state)
    if not d:
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    await state.update_data(editing=True)
    await _edit(cb, "✏️ <b>شو بدك تعدّل؟</b>", K.ds_edit_menu(DS.needs_media(d["items"]), DS.has_video(d["items"])))
    await cb.answer()


@router.callback_query(F.data.startswith("ds:back:"))
async def cb_back(cb: CallbackQuery, state: FSMContext) -> None:
    where = cb.data.split(":")[2]
    d = await _ensure(state)
    if not d:
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    if where == "svc":
        await state.clear()
        svc = await settings_repo.services()
        await _edit(cb, T.design_intro(), K.design_services(svc["addons"], svc["ai_reel"]))
    elif where == "biz":
        await _show_biz(cb, state)
    elif where == "msg":
        await _show_msg(cb, state)
    elif where == "media":
        await _show_media(cb, state)
    elif where == "brand":
        await _show_brand(cb, state)
    elif where == "lang":
        await _show_lang(cb, state)
    elif where == "extras":
        await _show_extras(cb, state)
    elif where == "notes":
        await _show_notes(cb, state)
    else:
        await _show_summary(cb, state)
    await cb.answer()


@router.callback_query(F.data == "ds:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("draft_id"):
        await orders_repo.cancel_awaiting(d["draft_id"], cb.from_user.id)
    await state.clear()
    await _edit(cb, "تم إلغاء الطلب — ما انخصم شي ✅", K.home_only())
    await cb.answer()


# ───────────── التأكيد ─────────────

@router.callback_query(Design.choosing, F.data == "ds:confirm")
async def cb_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    d = await _ensure(state)
    if not d or not d.get("message"):
        await cb.answer("الطلب ناقص — راجع الملخص", show_alert=True)
        return
    if CP.maintenance_text():
        await cb.answer(CP.maintenance_text(), show_alert=True)
        return
    if not await _enabled():
        await cb.answer(T.LOCKED_SERVICE, show_alert=True)
        return
    spec = DS.build_spec(d)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    try:
        order = await orders_svc.confirm(cb.from_user.id, spec, d.get("draft_id"))
    except InsufficientBalance:
        await cb.answer(T.META_INSUFFICIENT_RACE, show_alert=True)
        await _show_summary(cb, state)
        return
    media = list(d.get("media") or [])
    b = d.get("brand") or {}
    if b.get("logo"):
        media.append(["photo", b["logo"]])   # اللوغو يصل الأدمن مع ملفات العميل
    if media:
        await orders_repo.add_media(order["id"], [(k, f) for k, f in media])
    await state.clear()
    await cb.answer("✅ تم الخصم — استلمنا طلبك")
    balance = await users_repo.get_balance(cb.from_user.id)
    text = T.DS_DONE.format(id=order["id"], price=fmt(order["price_usd"]), balance=fmt(balance), hours=spec["deliver_hours"])
    try:
        await cb.message.edit_text(text, reply_markup=K.ds_done(order["id"]))
        sent = cb.message
    except Exception:  # noqa: BLE001
        sent = await cb.message.answer(text, reply_markup=K.ds_done(order["id"]))
    if sent:
        await orders_repo.set_messages(order["id"], user_msg_id=sent.message_id)
    from app.services import order_notify
    await order_notify.notify_admins_new_order(cb.bot, order["id"])


# ───────────── استئناف مسودة (يستدعيه ord:resume) ─────────────

async def resume_draft(cb: CallbackQuery, state: FSMContext, draft: dict) -> None:
    spec = draft["spec"]
    await state.clear()
    await state.update_data(
        kind="design", items=spec.get("items") or [], bundle_idx=spec.get("bundle_idx"), business=spec.get("business"),
        message=spec.get("message"), media=spec.get("media") or [], no_media=spec.get("no_media", False),
        brand=spec.get("brand") or {}, lang=spec.get("lang", "levant"), tone=spec.get("tone", "fun"),
        extras=spec.get("extras") or [], notes=spec.get("notes"), draft_id=draft["id"],
    )
    try:
        await cb.message.answer(T.META_DRAFT_RESUME.format(id=draft["id"]))
    except Exception:  # noqa: BLE001
        pass
    await _show_summary(cb, state, new_message=True)


# ───────────── بعد التسليم: اعتماد / تعديل / إلغاء / إعادة الملفات ─────────────

async def _client_order(cb: CallbackQuery, oid: int, statuses: tuple[str, ...], late_msg: str) -> dict | None:
    o = await orders_repo.get(oid)
    if not o or o["user_id"] != cb.from_user.id or o.get("kind") != "design":
        await cb.answer()
        return None
    if o["status"] not in statuses:
        await cb.answer(late_msg, show_alert=True)
        return None
    return o


@router.callback_query(F.data.regexp(r"^ds:approve:(\d+)$"))
async def cb_approve(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    if not await _client_order(cb, oid, ("delivered",), T.DS_REVISION_LATE):
        return
    o = await DS.approve(oid, cb.from_user.id)
    if not o:
        await cb.answer(T.DS_REVISION_LATE, show_alert=True)
        return
    await cb.answer("✅ شكراً!")
    o["media_count"] = len(await orders_repo.media(oid))
    await _edit(cb, T.DS_APPROVED.format(id=oid), K.ds_order_view(o))
    from app.services import order_notify
    await order_notify.refresh_admin_cards(cb.bot, oid)
    await order_notify.notify_admins_text(cb.bot, T.ADMIN_DS_APPROVED_NOTICE.format(id=oid, price=fmt(o["price_usd"])))


@router.callback_query(F.data.regexp(r"^ds:revise:(\d+)$"))
async def cb_revise(cb: CallbackQuery, state: FSMContext) -> None:
    oid = int(cb.data.split(":")[2])
    o = await _client_order(cb, oid, ("delivered",), T.DS_REVISION_LATE)
    if not o:
        return
    await state.set_state(Design.revision)
    if DS.next_revision_is_free(o):
        await state.update_data(rev_oid=oid, rev_fee="0")
        await cb.message.answer(T.DS_REVISION_ASK_FREE.format(id=oid), reply_markup=K.cancel_input(f"ord:view:{oid}"))
    else:
        fee = DS.revision_fee(o)
        balance = await users_repo.get_balance(cb.from_user.id)
        if balance < fee:
            await state.clear()
            await cb.message.answer(T.DS_REVISION_GAP.format(balance=fmt(balance), fee=fmt(fee)), reply_markup=K.balance_menu())
            await cb.answer()
            return
        await state.update_data(rev_oid=oid, rev_fee=str(fee))
        await cb.message.answer(T.DS_REVISION_ASK_PAID.format(fee=fmt(fee), pct=DS.pct_label()),
                                reply_markup=K.cancel_input(f"ord:view:{oid}"))
    await cb.answer()


@router.message(Design.revision, F.text)
async def msg_revision(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    txt = " ".join(message.text.split())
    if len(txt) < 5:
        await message.answer("اكتب شو بدك نعدّل بوضوح أكثر 🙂")
        return
    if len(txt) > DS.MAX_MESSAGE:
        await message.answer(T.DS_MESSAGE_LONG.format(n=len(txt)))
        return
    d = await state.get_data()
    oid = int(d.get("rev_oid") or 0)
    fee = Decimal(d.get("rev_fee") or "0")
    if fee > 0:
        # مدفوع: تأكيد صريح قبل الخصم
        await state.update_data(rev_text=txt)
        balance = await users_repo.get_balance(message.from_user.id)
        await message.answer(T.DS_REVISION_CONFIRM.format(fee=fmt(fee), text=T.esc(txt), balance=fmt(balance)),
                             reply_markup=K.ds_revision_confirm(oid, fmt(fee)))
        return
    await state.clear()
    await _apply_revision(message, oid, message.from_user.id, txt)


@router.callback_query(Design.revision, F.data.regexp(r"^ds:revise_pay:(\d+)$"))
async def cb_revise_pay(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    oid = int(cb.data.split(":")[2])
    txt = d.get("rev_text")
    await state.clear()
    if not txt or int(d.get("rev_oid") or 0) != oid:
        await cb.answer("انتهت الجلسة — اطلب التعديل من جديد", show_alert=True)
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await _apply_revision(cb.message, oid, cb.from_user.id, txt)
    await cb.answer()


async def _apply_revision(message: Message, oid: int, uid: int, txt: str) -> None:
    try:
        o, fee = await DS.request_revision(oid, uid, txt)
    except InsufficientBalance:
        o, fee = None, Decimal("0")
        await message.answer(T.DS_REVISION_GAP.format(balance=fmt(await users_repo.get_balance(uid)), fee=fmt(DS.revision_fee(await orders_repo.get(oid)))),
                             reply_markup=K.balance_menu())
        return
    if not o:
        await message.answer(T.DS_REVISION_LATE)
        return
    o["media_count"] = len(await orders_repo.media(oid))
    await message.answer(T.DS_REVISION_SENT.format(fee=T.DS_REVISION_FEE_LINE.format(fee=fmt(fee)) if fee > 0 else ""),
                         reply_markup=K.ds_order_view(o))
    from app.services import order_notify
    await order_notify.refresh_admin_cards(message.bot, oid)
    await order_notify.notify_admins_text(message.bot, T.ADMIN_DS_REVISION_NOTICE.format(
        id=oid, text=T.esc(txt), fee=f" (مدفوع {fmt(fee)})" if fee > 0 else " (مجاني)"))


@router.callback_query(F.data.regexp(r"^ds:files:(\d+)$"))
async def cb_files(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await _client_order(cb, oid, ("delivered", "completed"), "لا تسليم بعد.")
    if not o:
        return
    from app.services import order_notify
    n = await order_notify.send_delivery_files(cb.bot, cb.from_user.id, o)
    await cb.answer(f"📥 أُرسل {n} ملف" if n else "لا ملفات")


@router.callback_query(F.data.regexp(r"^ds:cancel_order:(\d+)$"))
async def cb_cancel_order(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await _client_order(cb, oid, ("submitted",), T.DS_CANCEL_LATE)
    if not o:
        return
    await _edit(cb, T.DS_CANCEL_CONFIRM.format(id=oid, price=fmt(o["price_usd"])), K.ds_cancel_confirm(oid))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^ds:cancel_yes:(\d+)$"))
async def cb_cancel_yes(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await DS.cancel_by_user(oid, cb.from_user.id)
    if not o:
        await cb.answer(T.DS_CANCEL_LATE, show_alert=True)
        return
    balance = await users_repo.get_balance(cb.from_user.id)
    o["media_count"] = len(await orders_repo.media(oid))
    await _edit(cb, T.DS_CANCEL_DONE.format(id=oid, price=fmt(o["refunded_usd"]), balance=fmt(balance)), K.ds_order_view(o))
    await cb.answer("↩️ استُرد المبلغ")
    from app.services import order_notify
    await order_notify.refresh_admin_cards(cb.bot, oid)
    await order_notify.notify_admins_text(cb.bot, f"🚫 <b>#ORD-{oid}</b>: العميل ألغى طلب التصميم قبل بدء العمل — استُرد {fmt(o['refunded_usd'])}.")
