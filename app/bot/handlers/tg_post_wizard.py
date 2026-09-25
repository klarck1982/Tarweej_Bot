"""📝 القنوات الشريكة — معالج الطلب TP1–TP7 (النشر يدوي بالتنسيق مع صاحب القناة).

TP1 الفئة ← TP2 القناة + بطاقتها ← TP3 الصيغة (24 س / 48 س / مثبَّت) ← TP4 المحتوى (نص + صور/فيديو أو «اكتبولي +5$»)
← TP5 الموعد (أقرب وقت / وقت محدد) ← TP6 الملخص والتأكيد (الخصم هنا فقط) ← TP7 تم.
رصيد ناقص → مسودة awaiting_payment + «اشحن الفرق» (نفس آلية Meta/Telegram Ads؛ الاستئناف من ord:resume).
"""

from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import checkout as CO
from app.bot import keyboards as K
from app.bot import texts as T
from app.db.repo import events, orders as orders_repo, partner_channels as PC, settings as settings_repo, users as users_repo
from app.bot import mp_texts as TX
from app.services import cpanel as CP, marketplace as MP, orders as orders_svc, partner_posts as PP, pricing as P, validators as V
from app.services.money import InsufficientBalance
from app.services.pricing import fmt, money

router = Router(name="tg_post_wizard")
PAGE = 6
MAX_MEDIA = 5
MAX_TEXT = 1000


class TgPost(StatesGroup):
    choosing = State()      # شاشات الأزرار (الفئة/القناة/الصيغة/الموعد/الملخص)
    content = State()       # ينتظر نصاً/صورة/فيديو
    when = State()          # ينتظر موعداً مكتوباً


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


async def is_open() -> tuple[bool, int]:
    """هل المسار مفتوح للعملاء؟ (الخدمة مفعّلة + قناة حيّة واحدة على الأقل)"""
    svc = await settings_repo.services()
    n = await PC.count_live()
    return bool(svc.get("tg_post", True)) and n > 0, n


# ───────────── TP1 الفئة ─────────────

async def _show_cats(target, state: FSMContext, new_message: bool = False) -> None:
    cats = await PC.live_categories()
    total = sum(n for _, n in cats)
    await state.set_state(TgPost.choosing)
    await _send(target, T.TGP_INTRO, K.tgp_categories(cats, total), new_message)


@router.callback_query(F.data == "tgp:start")
async def cb_start(cb: CallbackQuery, state: FSMContext) -> None:
    ok, _ = await is_open()
    await events.log_event("tg_track_click", cb.from_user.id, track="tg_post")
    if not ok:
        await cb.answer(T.TGP_SOON, show_alert=True)
        return
    if CP.maintenance_text():
        await cb.answer(CP.maintenance_text(), show_alert=True)
        return
    await state.clear()
    await state.update_data(kind="tg_post", media=[], addons=[])
    await _show_cats(cb, state)
    await cb.answer()


# ───────────── TP2 القناة ─────────────

def _chan_btn(ch: dict) -> str:
    q = PP.quote(ch, "24h")
    price = fmt(q[0]) if q else "—"
    return f"📢 {ch['title']} · {PP.subs_label(ch['subscribers'])} · {price}"


async def _show_channels(cb: CallbackQuery, state: FSMContext, cat: str, page: int = 1) -> None:
    items = await PC.list_live(None if cat == "all" else cat)
    if not items:
        await cb.answer(T.TGP_CAT_EMPTY, show_alert=True)
        await _show_cats(cb, state)
        return
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = min(max(1, page), pages)
    chunk = items[(page - 1) * PAGE: page * PAGE]
    await state.set_state(TgPost.choosing)
    await state.update_data(cat=cat)
    title = "📋 كل القنوات" if cat == "all" else PC.cat_label(cat)
    await _edit(cb, T.TGP_CAT_LIST.format(cat=f"<b>{title}</b>"), K.tgp_channels([(c["id"], _chan_btn(c)) for c in chunk], cat, page, pages))


@router.callback_query(F.data.startswith("tgp:cat:"))
async def cb_cat(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    cat = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 1
    d = await state.get_data()
    if d.get("kind") != "tg_post":
        await state.clear()
        await state.update_data(kind="tg_post", media=[], addons=[])
    await _show_channels(cb, state, cat, page)
    await cb.answer()


def channel_card_text(ch: dict) -> str:
    views = f" · 👁️ ~{PP.subs_label(ch['avg_views'])} مشاهدة للمنشور" if ch.get("avg_views") else ""
    blurb = f"<i>«{T.esc(ch['blurb'])}»</i>\n" if ch.get("blurb") else ""
    formats = " · ".join(f"{PP.fmt_label(code)}: <b>{fmt(price)}</b>" for code, price, _ in PP.formats_for(ch))
    if MP.is_mp_channel(ch):
        # 💼 قناة سوق: موثّقة من تيليغرام + نشر/حذف تلقائي + تقييم العملاء
        r = MP.rating_label(ch)
        done = f" · ✅ {ch['done_n']} منشوراً" if ch.get("done_n") else ""
        blurb = TX.CARD_BADGES.format(rating=f" · {r}" if r else "", done=done) + blurb
    return T.TGP_CHANNEL_CARD.format(
        title=T.esc(ch["title"]), username=f"<code>@{ch['username']}</code>" if ch.get("username") else "",
        subs=PP.subs_label(ch["subscribers"]), views=views, cat=PC.cat_label(ch["category"]), blurb=blurb, formats=formats,
    )


@router.callback_query(F.data.regexp(r"^tgp:ch:(\d+)$"))
async def cb_channel(cb: CallbackQuery, state: FSMContext) -> None:
    cid = int(cb.data.split(":")[2])
    ch = await PC.get(cid)
    if not ch or not ch["enabled"] or ch["archived"]:
        await cb.answer(T.TGP_CHANNEL_GONE, show_alert=True)
        return
    d = await state.get_data()
    if d.get("kind") != "tg_post":
        await state.clear()
        await state.update_data(kind="tg_post", media=[], addons=[])
    await state.set_state(TgPost.choosing)
    await _edit(cb, channel_card_text(ch), K.tgp_channel_card(cid, ch["url"], d.get("cat") or ch["category"]))
    await cb.answer()


# ───────────── TP3 الصيغة ─────────────

async def _show_formats(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    ch = await PC.get(int(d["channel_id"]))
    if not ch or not ch["enabled"] or ch["archived"]:
        await _send(target, T.TGP_CHANNEL_GONE, K.home_only(), new_message)
        return
    await state.set_state(TgPost.choosing)
    fmts = [(code, f"{PP.fmt_label(code)} — {fmt(price)}") for code, price, _ in PP.formats_for(ch)]
    await _send(target, T.TGP_FORMAT.format(title=T.esc(ch["title"])), K.tgp_formats(fmts, ch["id"]), new_message)


@router.callback_query(F.data.regexp(r"^tgp:pick:(\d+)$"))
async def cb_pick(cb: CallbackQuery, state: FSMContext) -> None:
    cid = int(cb.data.split(":")[2])
    ch = await PC.get(cid)
    if not ch or not ch["enabled"] or ch["archived"]:
        await cb.answer(T.TGP_CHANNEL_GONE, show_alert=True)
        return
    d = await state.get_data()
    if d.get("kind") != "tg_post":
        await state.clear()
        await state.update_data(kind="tg_post", media=[], addons=[])
    await state.update_data(channel_id=cid)
    if d.get("editing"):
        # غيّر القناة أثناء التعديل — الصيغة قد لا تكون متاحة عند القناة الجديدة
        if d.get("format") and PP.quote(ch, d["format"]):
            await _show_summary(cb, state)
            await cb.answer()
            return
    await _show_formats(cb, state)
    await cb.answer()


@router.callback_query(TgPost.choosing, F.data.regexp(r"^tgp:fmt:(\w+)$"))
async def cb_format(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data.split(":")[2]
    d = await state.get_data()
    ch = await PC.get(int(d.get("channel_id") or 0))
    if code not in PP.FORMATS or not ch or not PP.quote(ch, code):
        await cb.answer("هذه الصيغة غير متاحة لهذه القناة", show_alert=True)
        return
    await state.update_data(format=code)
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_content(cb, state)
    await cb.answer()


# ───────────── TP4 المحتوى ─────────────

def _content_label(d: dict) -> str:
    parts = []
    if d.get("text"):
        parts.append(f"نص {len(d['text'])} حرفاً")
    elif "copy" in (d.get("addons") or []):
        parts.append("النص يكتبه فريقنا ✍️")
    n = len(d.get("media") or [])
    if n:
        kinds = [m[0] for m in d["media"]]
        photos, videos = kinds.count("photo"), kinds.count("video")
        if photos:
            parts.append(f"{photos} صورة" if photos == 1 else f"{photos} صور")
        if videos:
            parts.append(f"{videos} فيديو")
    return " + ".join(parts) if parts else "—"


def _content_ready(d: dict) -> bool:
    return bool(d.get("text")) or "copy" in (d.get("addons") or [])


async def _show_content(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(TgPost.content)
    has_copy = "copy" in (d.get("addons") or [])
    if _content_ready(d) or d.get("media"):
        text = T.TGP_CONTENT_GOT.format(what=_content_label(d))
        kb = K.tgp_content_next(_content_ready(d))
    else:
        text = T.TGP_CONTENT
        kb = K.tgp_content(fmt(P.ADDONS["copy"]["price"]), has_copy)
    await _send(target, text, kb, new_message)


@router.callback_query(TgPost.content, F.data == "tgp:addon:copy")
async def cb_addon_copy(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    addons = list(d.get("addons") or [])
    if "copy" not in addons:
        addons.append("copy")
    await state.update_data(addons=addons, text=None)
    await _edit(cb, T.TGP_COPY_ADDED.format(price=fmt(P.ADDONS["copy"]["price"])), K.tgp_content_next(True))
    await cb.answer("✍️ أُضيفت كتابة النص")


@router.callback_query(TgPost.content, F.data == "tgp:content:reset")
async def cb_content_reset(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    await state.update_data(text=None, media=[], addons=[a for a in (d.get("addons") or []) if a != "copy"])
    await _show_content(cb, state)
    await cb.answer("🗑️ مُسح")


@router.callback_query(TgPost.content, F.data == "tgp:content:next")
async def cb_content_next(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if not _content_ready(d):
        await cb.answer("أرسل نص المنشور أولاً (أو اطلب كتابته)", show_alert=True)
        return
    if d.get("editing"):
        await _show_summary(cb, state)
    else:
        await _show_when(cb, state)
    await cb.answer()


@router.message(TgPost.content, F.photo | F.video | F.document)
async def msg_media(message: Message, state: FSMContext) -> None:
    d = await state.get_data()
    media = list(d.get("media") or [])
    if len(media) >= MAX_MEDIA:
        await message.answer(T.TGP_MEDIA_MAX)
        return
    if message.photo:
        media.append(["photo", message.photo[-1].file_id])
    elif message.video:
        media.append(["video", message.video.file_id])
    elif message.document and (message.document.mime_type or "").startswith(("image/", "video/")):
        media.append(["document", message.document.file_id])
    else:
        await message.answer("أرسل صورة أو فيديو فقط 🙂")
        return
    upd = {"media": media}
    caption = " ".join((message.caption or "").split())
    if caption and not d.get("text") and "copy" not in (d.get("addons") or []):
        if len(caption) > MAX_TEXT:
            await message.answer(T.TGP_TEXT_TOO_LONG.format(n=len(caption)))
        else:
            upd["text"] = caption
    await state.update_data(**upd)
    d = await state.get_data()
    if _content_ready(d):
        await message.answer(T.TGP_CONTENT_GOT.format(what=_content_label(d)), reply_markup=K.tgp_content_next(True))
    else:
        await message.answer(T.TGP_CONTENT_MEDIA_ONLY, reply_markup=K.tgp_content(fmt(P.ADDONS["copy"]["price"]), False))


@router.message(TgPost.content, F.text)
async def msg_text(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    txt = message.text.strip()
    if len(txt) > MAX_TEXT:
        await message.answer(T.TGP_TEXT_TOO_LONG.format(n=len(txt)))
        return
    if len(txt) < 5:
        await message.answer("النص قصير جداً — اكتب المنشور كاملاً كما تريده أن يظهر 🙂")
        return
    d = await state.get_data()
    await state.update_data(text=txt, addons=[a for a in (d.get("addons") or []) if a != "copy"])
    d = await state.get_data()
    await message.answer(T.TGP_CONTENT_GOT.format(what=_content_label(d)), reply_markup=K.tgp_content_next(True))


# ───────────── TP5 الموعد ─────────────

async def _show_when(target, state: FSMContext, new_message: bool = False) -> None:
    await state.set_state(TgPost.choosing)
    await _send(target, T.TGP_WHEN, K.tgp_when(), new_message)


@router.callback_query(TgPost.choosing, F.data == "tgp:when:asap")
async def cb_when_asap(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(when=None)
    await _show_summary(cb, state)
    await cb.answer()


@router.callback_query(TgPost.choosing, F.data == "tgp:when:type")
async def cb_when_type(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TgPost.when)
    await cb.message.answer(T.TGP_WHEN_TYPE, reply_markup=K.cancel_input("tgp:back:when"))
    await cb.answer()


@router.message(TgPost.when, F.text)
async def msg_when(message: Message, state: FSMContext) -> None:
    if _skip_text(message):
        await state.clear()
        return
    when = " ".join(message.text.split())[:60]
    if len(when) < 3:
        await message.answer(T.TGP_WHEN_TYPE)
        return
    await state.update_data(when=when)
    await _show_summary(message, state, new_message=True)


# ───────────── TP6 الملخص ─────────────

async def _spec_from_state(d: dict, tg_username: str | None) -> dict | None:
    ch = await PC.get(int(d.get("channel_id") or 0))
    if not ch or not d.get("format") or not PP.quote(ch, d["format"]):
        return None
    return PP.build_spec(ch, d["format"], d.get("text"), d.get("media") or [], d.get("when"), d.get("addons") or [],
                         V.clean_username(tg_username))


def summary_text(spec: dict, d: dict, price: Decimal, balance: Decimal) -> tuple[str, bool, Decimal]:
    ok = balance >= price
    gap = money(price - balance) if not ok else Decimal("0")
    addons = f"\n✍️ إضافات: <b>كتابة النص</b> (+{fmt(P.ADDONS['copy']['price'])})" if "copy" in (spec.get("addons") or []) else ""
    footer = T.TGP_SUMMARY_FOOTER_OK if ok else T.META_SUMMARY_GAP.format(gap=fmt(gap))
    text = T.TGP_SUMMARY.format(
        title=T.esc(spec["channel_title"]), subs=PP.subs_label(spec["channel_subs"]), format=PP.fmt_label(spec["format"]),
        content=T.esc(_content_label(d)), when=T.esc(spec.get("when")) if spec.get("when") else "⚡ أقرب وقت متاح",
        addons=addons, price=fmt(price), balance=fmt(balance), footer=footer,
    )
    return text, ok, gap


async def _show_summary(target, state: FSMContext, new_message: bool = False) -> None:
    d = await state.get_data()
    await state.set_state(TgPost.choosing)
    await CO.ensure_token(state)   # رمز الشراء — يمنع الخصم المكرر (v0.9.2)
    await state.update_data(editing=False)
    spec = await _spec_from_state(d, target.from_user.username)
    if not spec:
        await _send(target, T.TGP_CHANNEL_GONE, K.home_only(), new_message)
        return
    _, price, cost = orders_svc.compute_prices(spec)
    uid = target.from_user.id
    balance = await users_repo.get_balance(uid)
    text, ok, gap = summary_text(spec, d, price, balance)
    kb = K.tgp_summary(ok, fmt(price), fmt(gap) if not ok else None)
    if not ok:
        days_valid = int(await settings_repo.get("order_draft_days", 7))
        draft = await orders_repo.save_awaiting(uid, {**spec, "media": d.get("media") or []}, price, cost, days_valid,
                                                d.get("draft_id"), kind="tg_post")
        await state.update_data(draft_id=draft["id"], gap_usd=str(gap))
    await _send(target, text, kb, new_message)


@router.callback_query(TgPost.choosing, F.data == "tgp:edit")
async def cb_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(editing=True)
    await _edit(cb, "✏️ <b>شو بدك تعدّل؟</b>", K.tgp_edit_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("tgp:back:"))
async def cb_back(cb: CallbackQuery, state: FSMContext) -> None:
    where = cb.data.split(":")[2]
    d = await state.get_data()
    if d.get("kind") != "tg_post":
        await cb.answer("انتهت الجلسة — ابدأ من جديد", show_alert=True)
        return
    if where == "cats":
        await _show_cats(cb, state)
    elif where == "fmt":
        if d.get("channel_id"):
            await _show_formats(cb, state)
        else:
            await _show_cats(cb, state)
    elif where == "content":
        await _show_content(cb, state)
    elif where == "when":
        await _show_when(cb, state)
    elif where == "summary":
        await _show_summary(cb, state)
    await cb.answer()


@router.callback_query(F.data == "tgp:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d.get("draft_id"):
        await orders_repo.cancel_awaiting(d["draft_id"], cb.from_user.id)
    await state.clear()
    await _edit(cb, "تم إلغاء الطلب — ما انخصم شي ✅", K.home_only())
    await cb.answer()


# ───────────── التأكيد ─────────────

@router.callback_query(TgPost.choosing, F.data == "tgp:confirm")
async def cb_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if not d.get("channel_id") or not d.get("format") or not _content_ready(d):
        await cb.answer("الطلب ناقص — راجع الملخص", show_alert=True)
        return
    if CP.maintenance_text():
        await cb.answer(CP.maintenance_text(), show_alert=True)
        return
    ok, _ = await is_open()
    spec = await _spec_from_state(d, cb.from_user.username)
    if not ok or not spec:
        await cb.answer(T.TGP_CHANNEL_GONE, show_alert=True)
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    try:
        order = await orders_svc.confirm(cb.from_user.id, spec, d.get("draft_id"), CO.key_for(cb, d))
    except InsufficientBalance:
        await cb.answer(T.META_INSUFFICIENT_RACE, show_alert=True)
        await _show_summary(cb, state)
        return
    if order.get("duplicate"):
        # ضغطة مكررة/متزامنة: الطلب الأول أُنشئ وخُصم مرة واحدة — لا نكرر الإشعارات ولا الملفات
        await CO.answer_duplicate(cb, order)
        return
    media = d.get("media") or []
    if media:
        await orders_repo.add_media(order["id"], [(k, f) for k, f in media])
    await state.clear()
    await cb.answer("✅ تم الخصم — استلمنا طلبك")
    balance = await users_repo.get_balance(cb.from_user.id)
    text = T.TGP_DONE.format(id=order["id"], price=fmt(order["price_usd"]), balance=fmt(balance), title=T.esc(spec["channel_title"]))
    if MP.is_mp_channel(await PC.get(int(spec.get("channel_id") or 0))):
        # 💼 قناة سوق: صاحبها يقبل والبوت ينشر ويراقب — لا «نراجع ونؤكد» ولا «ملخص مشاهدات»
        title = T.esc(spec["channel_title"])
        step1 = (TX.TGP_DONE_MP_STEP1_COPY.format(title=title) if not spec.get("text")
                 else TX.TGP_DONE_MP_STEP1.format(title=title, hours=(await MP.cfg())["accept_hours"]))
        text = TX.TGP_DONE_MP.format(id=order["id"], price=fmt(order["price_usd"]), balance=fmt(balance), step1=step1)
    sent = None
    try:
        await cb.message.edit_text(text, reply_markup=K.tgp_done(order["id"]))
        sent = cb.message
    except Exception:  # noqa: BLE001
        sent = await cb.message.answer(text, reply_markup=K.tgp_done(order["id"]))
    if sent:
        await orders_repo.set_messages(order["id"], user_msg_id=sent.message_id)
    from app.services import order_notify, mp_notify
    # 💼 قناة سوق: نربط الطلب بصاحبها ونرسل له طلب القبول أولاً، فتخرج بطاقة الأدمن بصيغة السوق من البداية
    await mp_notify.after_customer_paid(cb.bot, order["id"])
    await order_notify.notify_admins_new_order(cb.bot, order["id"])


# ───────────── استئناف مسودة (يستدعيه ord:resume) ─────────────

async def resume_draft(cb: CallbackQuery, state: FSMContext, draft: dict) -> None:
    spec = draft["spec"]
    await state.clear()
    await state.update_data(
        kind="tg_post", channel_id=spec.get("channel_id"), format=spec.get("format"), text=spec.get("text"),
        media=spec.get("media") or [], when=spec.get("when"), addons=spec.get("addons") or [], draft_id=draft["id"],
    )
    try:
        await cb.message.answer(T.META_DRAFT_RESUME.format(id=draft["id"]))
    except Exception:  # noqa: BLE001
        pass
    await _show_summary(cb, state, new_message=True)


# ───────────── إلغاء العميل (مجاني قبل الجدولة) ─────────────

@router.callback_query(F.data.regexp(r"^tgp:cancel_order:(\d+)$"))
async def cb_cancel_order(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await orders_repo.get(oid)
    if not o or o["user_id"] != cb.from_user.id or o.get("kind") != "tg_post":
        await cb.answer()
        return
    if o["status"] != "submitted":
        await cb.answer(T.TGP_CANCEL_LATE, show_alert=True)
        return
    await _edit(cb, T.TGP_CANCEL_CONFIRM.format(id=oid, price=fmt(o["price_usd"])), K.tgp_cancel_confirm(oid))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^tgp:cancel_yes:(\d+)$"))
async def cb_cancel_yes(cb: CallbackQuery) -> None:
    oid = int(cb.data.split(":")[2])
    o = await PP.cancel_by_user(oid, cb.from_user.id)
    if not o:
        await cb.answer(T.TGP_CANCEL_LATE, show_alert=True)
        return
    balance = await users_repo.get_balance(cb.from_user.id)
    o["media_count"] = len(await orders_repo.media(oid))
    await _edit(cb, T.TGP_CANCEL_DONE.format(id=oid, price=fmt(o["refunded_usd"]), balance=fmt(balance)), K.tgp_order_view(o))
    await cb.answer("↩️ استُرد المبلغ")
    from app.services import order_notify
    await order_notify.refresh_admin_cards(cb.bot, oid)
    await order_notify.notify_admins_text(cb.bot, f"🚫 <b>#ORD-{oid}</b>: العميل ألغى طلب النشر قبل الجدولة — استُرد {fmt(o['refunded_usd'])}.")
    if o.get("owner_user_id") and o.get("owner_deadline"):
        from app.services import mp_notify
        await mp_notify.on_refunded(cb.bot, o, "cancel")
