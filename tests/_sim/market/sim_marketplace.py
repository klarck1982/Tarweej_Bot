"""محاكاة كاملة لسوق القنوات ذاتي الخدمة (v1.0) — عبر البوت الحقيقي + تيليغرام وهمي يحاكي القنوات فعلياً.

القنوات الوهمية لها: أعضاء بحالاتهم (مالك/مشرف/عضو) · صلاحيات البوت · عدد مشتركين · رسائل منشورة تُحذف وتُثبَّت.
التحقق من وجود المنشور (editMessageReplyMarkup) يرد كما يرد تيليغرام: «not modified» للموجود و«not found» للمحذوف.

السيناريوهات:
 A  التسجيل والتحقق (بلا صلاحية · قناة خاصة · مجموعة · غير مالك · إضافة البوت · أسعار خاطئة · قناة مسجّلة لغيرك)
 B  الموافقة ثم طلب كامل: قبول ← نشر فوري ← تحقق ← انتهاء وحذف ← ربح محجوز ← تقييم ← تحرير
 C  مثبّت + صورتان: مجموعة وسائط + تثبيت ← فك التثبيت والحذف عند الانتهاء
 D  اعتذار صاحب القناة · E انتهاء المهلة · F إلغاء العميل ثم ضغطة قبول قديمة
 G  حذف مبكر للمنشور ← استرداد كامل بلا ربح · H موعد لاحق · I فشل النشر ← إعادة ← استرداد بعد المهلة
 J  إزالة البوت من القناة وفيها طلبات جارية · K البلاغات (مقبول/مرفوض)
 L  السحب (عنوان خاطئ · تأكيد · منع سحب ثانٍ · دفع · رفض وإرجاع) · M التحويل إلى رصيد
 N  تسابقات: نشر مزدوج · قبول+إلغاء متزامنان · تحويل مزدوج · سحبان متزامنان · تحرير مزدوج
 O  «اكتبولي»: لا يصل صاحب القناة حتى يكتب الفريق النص · P رفض قناة ثم إعادة إرسال · Q إيقاف/إعادة
 R  إعدادات السوق من البوت · S الأمان (أزرار طلبات/قنوات الآخرين)
 Z  ثوابت: دفتر الرصيد = الأرصدة · دفتر الأرباح = أعمدة الأرباح · لا ربح لطلب مُسترد · لا منشورات يتيمة في القنوات

تشغيل: /tmp/freshdb.sh && /tmp/venv/bin/python tests/_sim/market/sim_marketplace.py
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "explore"))
from harness import *  # noqa: E402,F403
from harness import StrictSession, sent, _kb_buttons  # noqa: E402
from crawler import seed  # noqa: E402
from scenarios import drive, bal, qv, q1, expect  # noqa: E402
from app.services.marketplace import MPError  # noqa: E402

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # noqa: E402
from aiogram.types import (AcceptedGiftTypes, Chat, ChatFullInfo, ChatMemberAdministrator, ChatMemberLeft,  # noqa: E402
                           ChatMemberMember, ChatMemberOwner, ChatMemberUpdated, Message, Update, User)

D = Decimal
BOT_ID = 123456
OWNER, OWNER2, STRANGER = 700, 701, 702
CH_MAIN, CH_PRIV, CH_GROUP, CH_B = -1002000000001, -1002000000002, -1002000000003, -1002000000004
_cmid = itertools.count(5000)
_upd = itertools.count(900_000)
STEP = {"n": 0}

# ───────────────────────── قنوات تيليغرام الوهمية ─────────────────────────


def _admin(uid, post=True, delete=True, edit=True, bot=False):
    return ChatMemberAdministrator(user=User(id=uid, is_bot=bot, first_name="x"), can_be_edited=False, is_anonymous=False,
                                   can_manage_chat=True, can_delete_messages=delete, can_manage_video_chats=False,
                                   can_restrict_members=False, can_promote_members=False, can_change_info=False,
                                   can_invite_users=False, can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                                   can_send_welcome_messages=False, can_post_messages=post, can_edit_messages=edit)


FAKE = {
    CH_MAIN: {"type": "channel", "username": "damascus_deals", "title": "عروض دمشق", "count": 20500,
              "members": {OWNER: "creator", STRANGER: "member"}, "bot": None, "msgs": {}, "pinned": set()},
    CH_PRIV: {"type": "channel", "username": None, "title": "قناة خاصة", "count": 900,
              "members": {OWNER: "creator"}, "bot": {"post": True, "delete": True, "edit": True}, "msgs": {}, "pinned": set()},
    CH_GROUP: {"type": "supergroup", "username": "some_group", "title": "مجموعة", "count": 300,
               "members": {OWNER: "creator"}, "bot": {"post": True, "delete": True, "edit": True}, "msgs": {}, "pinned": set()},
    CH_B: {"type": "channel", "username": "aleppo_tech", "title": "تقنية حلب", "count": 8000,
           "members": {OWNER2: "creator", OWNER: "administrator"}, "bot": {"post": True, "delete": True, "edit": False},
           "msgs": {}, "pinned": set()},
}
BY_NAME = {v["username"].lower(): k for k, v in FAKE.items() if v["username"]}


def _chat_of(ref):
    if isinstance(ref, str) and ref.startswith("@"):
        return BY_NAME.get(ref[1:].lower())
    try:
        ref = int(ref)
    except (TypeError, ValueError):
        return None
    return ref if ref in FAKE else None


class MarketSession(StrictSession):
    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        d = method.model_dump(exclude_none=True)
        from aiogram.client.default import Default
        for k, v in list(d.items()):
            if isinstance(v, Default):
                d[k] = "HTML" if k == "parse_mode" else None
        cid = _chat_of(d.get("chat_id"))
        if cid is None:
            return await super().make_request(bot, method, timeout)
        ch = FAKE[cid]
        sent.append((name, d))
        self._validate(name, d)
        if name == "GetChat":
            return ChatFullInfo(id=cid, type=ch["type"], title=ch["title"], username=ch["username"], accent_color_id=0,
                                max_reaction_count=0, accepted_gift_types=AcceptedGiftTypes(
                                    unlimited_gifts=False, limited_gifts=False, unique_gifts=False, premium_subscription=False,
                                    gifts_from_channels=False))
        if name == "GetChatMember":
            uid = int(d["user_id"])
            if uid == BOT_ID:
                if not ch["bot"]:
                    return ChatMemberLeft(user=User(id=uid, is_bot=True, first_name="b"))
                r = ch["bot"]
                return _admin(uid, r["post"], r["delete"], r["edit"], bot=True)
            st = ch["members"].get(uid)
            u = User(id=uid, is_bot=False, first_name="x")
            if st == "creator":
                return ChatMemberOwner(user=u, is_anonymous=False)
            if st == "administrator":
                return _admin(uid)
            if st == "member":
                return ChatMemberMember(user=u)
            return ChatMemberLeft(user=u)
        if name == "GetChatMemberCount":
            return ch["count"]
        bot_ok = bool(ch["bot"]) and ch["bot"]["post"]
        if name in ("SendMessage", "SendPhoto", "SendVideo", "SendDocument", "SendMediaGroup"):
            if not bot_ok:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot is not a member of the channel chat")
            items = d.get("media") if name == "SendMediaGroup" else [d]
            out = []
            for it in items:
                mid = next(_cmid)
                ch["msgs"][mid] = {"kind": name, "text": it.get("text") or it.get("caption")}
                out.append(Message(message_id=mid, date=datetime.now(), chat=Chat(id=cid, type="channel"),
                                   text=it.get("text"), caption=it.get("caption")))
            return out if name == "SendMediaGroup" else out[0]
        if name == "EditMessageReplyMarkup":
            if not ch["bot"]:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            if int(d["message_id"]) in ch["msgs"]:
                raise TelegramBadRequest(method=method, message="Bad Request: message is not modified: specified new message content and reply markup are exactly the same")
            raise TelegramBadRequest(method=method, message="Bad Request: message to edit not found")
        if name == "DeleteMessage":
            if ch["msgs"].pop(int(d["message_id"]), None) is None:
                raise TelegramBadRequest(method=method, message="Bad Request: message to delete not found")
            return True
        if name == "PinChatMessage":
            if not (ch["bot"] or {}).get("edit"):
                raise TelegramBadRequest(method=method, message="Bad Request: not enough rights to manage pinned messages")
            ch["pinned"].add(int(d["message_id"]))
            return True
        if name == "UnpinChatMessage":
            ch["pinned"].discard(int(d.get("message_id") or 0))
            return True
        return True


async def chat_member_update(env, cid, by_uid, old_admin: bool, new_admin: bool):
    """تيليغرام يرسل my_chat_member حين يُضاف البوت مشرفاً أو يُزال."""
    ch = FAKE[cid]
    bot_user = User(id=BOT_ID, is_bot=True, first_name="b")
    if new_admin:
        ch["bot"] = ch["bot"] or {"post": True, "delete": True, "edit": True}
    else:
        ch["bot"] = None
    old = _admin(BOT_ID, bot=True) if old_admin else ChatMemberLeft(user=bot_user)
    new = _admin(BOT_ID, bot=True) if new_admin else ChatMemberLeft(user=bot_user)
    ev = ChatMemberUpdated(chat=Chat(id=cid, type=ch["type"], title=ch["title"], username=ch["username"]),
                           from_user=User(id=by_uid, is_bot=False, first_name="owner", username=f"u{by_uid}"),
                           date=datetime.now(), old_chat_member=old, new_chat_member=new)
    start = len(sent)
    ISSUES.context = f"my_chat_member {cid} {old_admin}->{new_admin}"  # noqa: F405
    await env.dp.feed_update(env.bot, Update(update_id=next(_upd), my_chat_member=ev))
    return sent[start:]


# ───────────────────────── أدوات ─────────────────────────

def texts_to(out, uid):
    return " ".join((d.get("text") or d.get("caption") or "") for n, d in out if str(d.get("chat_id")) == str(uid))


def alerts(out):
    return " ".join((d.get("text") or "") for n, d in out if n == "AnswerCallbackQuery")


def buttons_to(out, uid):
    return [b for n, d in out if str(d.get("chat_id")) == str(uid) for b in _kb_buttons(d.get("reply_markup"))]


def find_btn(out, uid, prefix):
    for b in buttons_to(out, uid):
        if (b.get("callback_data") or "").startswith(prefix):
            return b["callback_data"]
    return None


def section(t):
    STEP["n"] += 1
    print(f"\n══ {t}")


async def order_row(oid):
    from app.db.repo import orders as R
    return await R.get(oid)


async def tick(env):
    from app.services import scheduler
    start = len(sent)
    ISSUES.context = "scheduler.tick"  # noqa: F405
    await scheduler._marketplace(env.bot)
    return sent[start:]


async def age(oid, **cols):
    """يحرّك الزمن: يضبط أعمدة التوقيت لطلب (مثلاً ends_at = الماضي)."""
    sets = ", ".join(f"{k} = now() - interval '{v}'" for k, v in cols.items())
    from app.db import pool as db
    await db.execute(f"UPDATE orders SET {sets} WHERE id = $1", oid)


async def buy(u, cid, fmt="24h", text="عرض خاص: خصم 30% على كل الملابس الشتوية 🔥 <b>& أكثر</b>", photos=0, copy=False, when=None):
    """العميل يشتري منشوراً عبر المعالج الحقيقي. يعيد (رقم الطلب، مخرجات التأكيد)."""
    await drive(u, ["cb:nav:tg", "cb:tgp:start", "cb:tgp:cat:all", f"cb:tgp:ch:{cid}", f"cb:tgp:pick:{cid}", f"cb:tgp:fmt:{fmt}"])
    if copy:
        await drive(u, ["cb:tgp:addon:copy"])
    else:
        await drive(u, [f"t:{text}"])
    for _ in range(photos):
        await u.photo()
    await drive(u, ["cb:tgp:content:next"])
    if when:
        await drive(u, ["cb:tgp:when:type", f"t:{when}"])
    else:
        await drive(u, ["cb:tgp:when:asap"])
    out = await u.click("tgp:confirm")
    oid = await qv("SELECT max(id) FROM orders WHERE user_id = $1 AND kind = 'tg_post'", u.uid)  # noqa: F405
    return int(oid), out


async def invariants(tag):
    from app.db import pool as db
    from app.services import marketplace as MP
    bad = await db.fetch("SELECT u.tg_id, u.balance_usd, COALESCE(sum(l.amount_usd),0) s FROM users u LEFT JOIN ledger l "
                         "ON l.user_id = u.tg_id GROUP BY u.tg_id HAVING u.balance_usd <> COALESCE(sum(l.amount_usd),0)")
    expect(not bad, "critical", "ledger-mismatch", f"[{tag}] دفتر الرصيد ≠ الرصيد: {[dict(r) for r in bad]}")
    em = await MP.earnings_mismatch()
    expect(not em, "critical", "earnings-mismatch", f"[{tag}] دفتر الأرباح ≠ أعمدة الأرباح: {em}")
    dbl = await db.fetch("SELECT id, status, payout_status, refunded_usd FROM orders WHERE refunded_usd > 0 "
                         "AND payout_status IN ('held','available','disputed')")
    expect(not dbl, "critical", "paid-twice", f"[{tag}] طلب مُسترد للعميل وربحه محسوب للقناة: {[dict(r) for r in dbl]}")
    earn_dup = await db.fetch("SELECT ref_id, count(*) FROM earnings_ledger WHERE type = 'earning' GROUP BY ref_id HAVING count(*) > 1")
    expect(not earn_dup, "critical", "earning-dup", f"[{tag}] ربح مكرر لطلب: {[dict(r) for r in earn_dup]}")
    neg = await db.fetchval("SELECT count(*) FROM users WHERE balance_usd < 0 OR earn_held_usd < 0 OR earn_avail_usd < 0")
    expect(neg == 0, "critical", "negative", f"[{tag}] رصيد سالب")
    # منشورات يتيمة: رسالة في قناة وهمية لطلب ليس active
    live_ids = set()
    for r in await db.fetch("SELECT channel_msg_ids FROM orders WHERE status = 'active' AND channel_msg_ids IS NOT NULL"):
        ids = r["channel_msg_ids"]
        live_ids |= set(json.loads(ids) if isinstance(ids, str) else ids)
    orphans = {cid: [m for m in ch["msgs"] if m not in live_ids] for cid, ch in FAKE.items()}
    orphans = {k: v for k, v in orphans.items() if v}
    expect(not orphans, "high", "orphan-post", f"[{tag}] منشورات باقية في القنوات بلا طلب فعّال: {orphans}")


# ───────────────────────── السيناريوهات ─────────────────────────

async def main():
    env = await Env(verbose=bool(os.getenv("SIM_LOG")), logfile=os.getenv("SIM_LOG")).boot()  # noqa: F405
    from app.bot.wide import WideButtonsMiddleware
    env.bot.session = MarketSession()
    env.bot.session.middleware(WideButtonsMiddleware())
    await seed(env)
    from app.db import pool as db
    from app.db.repo import partner_channels as PC, users as users_repo
    from app.services import marketplace as MP, money, pricing as P

    for uid in (OWNER, OWNER2, STRANGER):
        await users_repo.upsert_user(uid, f"مالك {uid}", f"u{uid}")
    await money.credit(U, D("500"), "topup", note="seed")  # noqa: F405
    await money.credit(U2, D("200"), "topup", note="seed")  # noqa: F405
    ow, ow2, st = env.actor(OWNER, "صاحب-قناة"), env.actor(OWNER2, "صاحب-قناة2"), env.actor(STRANGER, "غريب")
    u, u2, a = env.actor(U, "عميل"), env.actor(U2, "عميل2"), env.actor(A, "أدمن")  # noqa: F405
    for x in (ow, ow2, st, u, u2):
        await drive(x, ["t:/start", "cb:nav:accept"])

    # ═══ A التسجيل ═══
    section("A) التسجيل والتحقق")
    out = await ow.click("mp:home")
    expect("اربح من قناتك" in texts_to(out, OWNER), "high", "mp-intro", "لا تظهر شاشة التعريف")
    await ow.press("سجّل قناتي")
    expect(any("startchannel" in (b.get("url") or "") for b in ow.last_buttons), "high", "add-link", "لا زر إضافة البوت للقناة")
    out = await ow.text("@damascus_deals")
    expect("ليس مشرفاً" in texts_to(out, OWNER), "high", "no-rights", f"قبل التسجيل والبوت غير مشرف: {texts_to(out, OWNER)[:120]}")
    out = await ow.text("https://t.me/+AbCdEfGhIj")
    expect("قناة خاصة" in texts_to(out, OWNER), "medium", "invite-link", "رابط دعوة خاصة لم يُرفض بوضوح")
    out = await ow.text("-1002000000002")   # قناة خاصة بلا @ (معرّف رقمي لا يُقبل نصاً)
    out = await ow.text("@some_group")
    expect("ليس قناة" in texts_to(out, OWNER), "high", "group", f"مجموعة قُبلت كقناة: {texts_to(out, OWNER)[:100]}")
    # قناة خاصة عبر إعادة توجيه (نحاكيها بـ my_chat_member)
    out = await chat_member_update(env, CH_PRIV, OWNER, False, True)
    expect("خاصة" in texts_to(out, OWNER), "high", "private", f"قناة خاصة قُبلت: {texts_to(out, OWNER)[:100]}")
    # غريب (عضو فقط) يحاول تسجيل قناة غيره بعد أن يكون البوت مشرفاً
    FAKE[CH_MAIN]["bot"] = {"post": True, "delete": True, "edit": True}
    await drive(st, ["cb:mp:add"])
    out = await st.text("@damascus_deals")
    expect("ليس مالك" in texts_to(out, STRANGER), "critical", "stranger-register", f"غير المالك سجّل القناة: {texts_to(out, STRANGER)[:100]}")
    n = await qv("SELECT count(*) FROM partner_channels WHERE owner_user_id = $1", STRANGER)  # noqa: F405
    expect(n == 0, "critical", "stranger-row", "أُنشئت قناة لغير مالكها")
    # المالك يضيف البوت ← تسجيل تلقائي
    FAKE[CH_MAIN]["bot"] = None
    out = await chat_member_update(env, CH_MAIN, OWNER, False, True)
    t = texts_to(out, OWNER)
    expect("تم التحقق" in t and "20.5 ألف" in t, "critical", "auto-verify", f"لم يبدأ التسجيل تلقائياً بعد إضافة البوت: {t[:160]}")
    cat_cb = find_btn(out, OWNER, "mp:cat:")
    expect(cat_cb is not None, "critical", "cat-buttons", "لا أزرار فئات")
    cid = int(cat_cb.split(":")[2])
    await ow.click(cat_cb.rsplit(":", 1)[0] + ":shopping")
    for bad_p, why in (("abc", "رقم"), ("0.1", "بين"), ("9999999", "بين")):
        out = await ow.text(bad_p)
        expect(why in texts_to(out, OWNER), "high", "price-validate", f"سعر خاطئ «{bad_p}» لم يُرفض")
    await drive(ow, ["t:١٠", "t:15", "t:14", "t:عروض ومتاجر دمشق يومياً <script>"])
    ch = await PC.get(cid)
    expect(ch["price_24h"] == D("10") and ch["price_48h"] == D("15") and ch["price_pin"] == D("14") and ch["allow_pin"],
           "critical", "prices-saved", f"الأسعار لم تُحفظ: {ch['price_24h']}/{ch['price_48h']}/{ch['price_pin']}/{ch['allow_pin']}")
    expect(ch["mp_status"] == "draft" and not ch["enabled"], "critical", "draft-hidden", "المسودة ظاهرة للعملاء")
    expect(ch["subscribers"] == 20500 and ch["chat_id"] == CH_MAIN, "high", "verified-subs", "عدد المشتركين ليس رقم تيليغرام")
    live = [c["id"] for c in await PC.list_live()]
    expect(cid not in live, "critical", "draft-live", "القناة ظاهرة قبل الموافقة")
    out = await ow.press("أوافق وأرسل")
    admin_card = [d for n, d in out if "قناة جديدة بانتظار الموافقة" in (d.get("text") or "")]
    expect(bool(admin_card), "critical", "admin-card", "لم تصل بطاقة القناة للأدمن")
    expect((await PC.get(cid))["mp_status"] == "pending", "critical", "pending", "لم تتحول إلى قيد المراجعة")
    out = await ow.click(f"mp:submit:{cid}", expect_ok=False)
    expect("مسبقاً" in alerts(out), "medium", "double-submit", "إرسال مزدوج بلا تنبيه")
    # قناة مسجّلة: مالك آخر (مشرف في CH_MAIN) يحاول
    FAKE[CH_MAIN]["members"][OWNER2] = "administrator"
    await drive(ow2, ["cb:mp:add"])
    out = await ow2.text("@damascus_deals")
    expect("حساب آخر" in texts_to(out, OWNER2), "critical", "dup-channel", f"قناة مسجّلة قُبلت لحساب ثانٍ: {texts_to(out, OWNER2)[:100]}")
    del FAKE[CH_MAIN]["members"][OWNER2]
    await drive(ow2, ["cb:mp:home"])
    print(f"    قناة #{cid}: {ch['title']} · {ch['subscribers']} مشترك · مسودة ← قيد المراجعة · الغريب/المكرر/الخاصة/المجموعة مرفوضة")

    # ═══ B موافقة + طلب كامل ═══
    section("B) الموافقة ثم طلب كامل حتى تحرير الربح")
    out = await a.click(f"adm:mp:ok:{cid}")
    expect("اعتُمدت" in texts_to(out, OWNER), "high", "approved-note", "صاحب القناة لم يُبلَّغ بالاعتماد")
    ch = await PC.get(cid)
    expect(ch["mp_status"] == "approved" and ch["enabled"], "critical", "approve", "لم تُعتمد")
    out = await a.click(f"adm:mp:ok:{cid}", expect_ok=False)
    expect("لم يُنفَّذ" in alerts(out), "medium", "double-approve", "اعتماد مزدوج بلا تنبيه")
    out = await u.click(f"tgp:ch:{cid}")
    expect("قناة موثّقة" in texts_to(out, U), "high", "badges", "بطاقة القناة بلا شارة التوثيق")  # noqa: F405
    client_price = P.money(D("10") * P.TG_POST_MULT)
    b0 = await bal(U)  # noqa: F405
    oid, out = await buy(u, cid)
    o = await order_row(oid)
    expect(o["status"] == "submitted" and o["owner_user_id"] == OWNER and o["owner_deadline"], "critical", "dispatch",
           f"الطلب لم يُربط بصاحب القناة: {o['status']} owner={o['owner_user_id']} dl={o['owner_deadline']}")
    expect(b0 - await bal(U) == client_price == o["price_usd"], "critical", "charge", f"خصم {b0 - await bal(U)} ≠ {client_price}")  # noqa: F405
    t = texts_to(out, OWNER)
    expect("طلب إعلان جديد" in t and "10$" in t, "critical", "owner-request", f"صاحب القناة لم يستلم الطلب بربحه الصافي: {t[:200]}")
    expect(str(client_price) not in t.replace("10.00", "") or client_price == D("10"), "medium", "commission-leak", "سعر العميل ظاهر لصاحب القناة")
    acc = find_btn(out, OWNER, f"mp:o:{oid}:acc")
    expect(acc is not None, "critical", "accept-btn", "لا زر قبول")
    await ow.click(acc)
    out = await ow.click(f"mp:o:{oid}:t:now")
    o = await order_row(oid)
    msgs = FAKE[CH_MAIN]["msgs"]
    expect(o["status"] == "active" and len(msgs) == 1, "critical", "publish", f"لم يُنشر: status={o['status']} msgs={len(msgs)}")
    posted = list(msgs.values())[0]["text"] if msgs else ""
    expect("&lt;b&gt;" in (posted or "") or "<b>" not in (posted or ""), "high", "escape", f"نص العميل لم يُهرَّب: {posted!r}")
    expect(o["post_url"] == f"https://t.me/damascus_deals/{o['channel_msg_ids'][0]}", "high", "post-url", f"رابط المنشور {o['post_url']}")
    expect("نُشر منشورك" in texts_to(out, U), "high", "customer-published", "العميل لم يستلم رابط المنشور")  # noqa: F405
    out = await ow.click(f"mp:o:{oid}:t:now", expect_ok=False)
    expect(len(FAKE[CH_MAIN]["msgs"]) == 1, "critical", "double-publish", "ضغطة قبول قديمة نشرت مرة ثانية")
    await age(oid, verified_at="4 hours")
    await tick(env)
    o = await order_row(oid)
    expect(o["status"] == "active" and o["verified_at"] > o["started_at"], "high", "verify-ok", "التحقق الدوري لم يعمل على منشور موجود")
    await age(oid, ends_at="1 minute")
    out = await tick(env)
    o = await order_row(oid)
    e = await MP.earnings(OWNER)
    expect(o["status"] == "completed" and o["unpublished_at"] and not FAKE[CH_MAIN]["msgs"], "critical", "finish-delete",
           f"الانتهاء/الحذف: {o['status']} unpub={o['unpublished_at']} msgs={len(FAKE[CH_MAIN]['msgs'])}")
    expect(e["held"] == D("10") and e["avail"] == 0 and o["payout_status"] == "held", "critical", "held",
           f"الربح المحجوز {e['held']} المتاح {e['avail']} {o['payout_status']}")
    expect(find_btn(out, U, f"mpc:rate:{oid}:5") is not None, "high", "rate-kb", "لم يُطلب تقييم من العميل")  # noqa: F405
    expect("أُضيف" in texts_to(out, OWNER), "high", "owner-completed", "صاحب القناة لم يُبلَّغ بالربح")
    await tick(env)   # تكرار الدورة لا يكرر الربح
    expect((await MP.earnings(OWNER))["held"] == D("10"), "critical", "held-dup", "الربح تكرر بتكرار الدورة")
    await u.click(f"mpc:rate:{oid}:5")
    out = await u.click(f"mpc:rate:{oid}:1", expect_ok=False)
    ch = await PC.get(cid)
    expect(ch["rating_n"] == 1 and ch["rating_sum"] == 5 and ch["done_n"] == 1, "high", "rating", f"التقييم {ch['rating_sum']}/{ch['rating_n']} منجز {ch['done_n']}")
    await age(oid, payout_at="1 minute")
    out = await tick(env)
    e = await MP.earnings(OWNER)
    expect(e["held"] == 0 and e["avail"] == D("10"), "critical", "release", f"التحرير: محجوز {e['held']} متاح {e['avail']}")
    expect("تحرّر" in texts_to(out, OWNER), "medium", "release-note", "لا إشعار بالتحرير")
    await invariants("B")
    print(f"    ORD-{oid}: العميل دفع {client_price} ← نُشر ← تحقق ← حُذف ← ربح 10$ محجوز ← ⭐5 ← متاح 10$")

    # ═══ C مثبّت + صورتان ═══
    section("C) مثبّت مع صورتين")
    oid_c, out = await buy(u, cid, fmt="pin", photos=2)
    t = texts_to(out, OWNER)
    expect("14" in t, "high", "pin-earn", "ربح التثبيت ليس 14$")
    await drive(ow, [f"cb:mp:o:{oid_c}:acc", f"cb:mp:o:{oid_c}:t:now"])
    o = await order_row(oid_c)
    expect(o["status"] == "active" and len(o["channel_msg_ids"]) == 2 and o["channel_msg_ids"][0] in FAKE[CH_MAIN]["pinned"],
           "critical", "pin-group", f"مجموعة+تثبيت: {o['status']} ids={o['channel_msg_ids']} pinned={FAKE[CH_MAIN]['pinned']}")
    await age(oid_c, ends_at="1 minute")
    await tick(env)
    expect(not FAKE[CH_MAIN]["msgs"] and not FAKE[CH_MAIN]["pinned"], "critical", "unpin-delete", "لم يُفك التثبيت/يُحذف")
    await invariants("C")
    print(f"    ORD-{oid_c}: مجموعة وسائط + تثبيت ← فُك وحُذف ✓")

    # ═══ D اعتذار ═══
    section("D) اعتذار صاحب القناة")
    b0 = await bal(U)  # noqa: F405
    oid_d, _ = await buy(u, cid)
    await drive(ow, [f"cb:mp:o:{oid_d}:rej", f"cb:mp:o:{oid_d}:r:aud"])
    o = await order_row(oid_d)
    expect(o["status"] == "rejected" and await bal(U) == b0 and (await PC.get(cid))["reject_n"] == 1, "critical", "reject",  # noqa: F405
           f"الاعتذار: {o['status']} رصيد {await bal(U)} vs {b0}")  # noqa: F405
    out = await ow.click(f"mp:o:{oid_d}:acc", expect_ok=False)
    expect("لم يعد بانتظار" in alerts(out), "medium", "stale-accept", "قبول طلب مُلغى بلا تنبيه")

    # ═══ E انتهاء المهلة ═══
    section("E) انتهاء مهلة الرد")
    b0 = await bal(U)  # noqa: F405
    oid_e, _ = await buy(u, cid)
    await tick(env)
    expect((await order_row(oid_e))["status"] == "submitted", "high", "early-timeout", "انتهت المهلة قبل وقتها")
    await age(oid_e, owner_deadline="1 minute")
    out = await tick(env)
    o = await order_row(oid_e)
    expect(o["status"] == "rejected" and await bal(U) == b0 and (await PC.get(cid))["timeout_n"] == 1, "critical", "timeout",  # noqa: F405
           f"المهلة: {o['status']}")
    expect("انتهت مهلة" in texts_to(out, OWNER), "medium", "timeout-note", "صاحب القناة لم يُبلَّغ بانتهاء المهلة")

    # ═══ F إلغاء العميل ═══
    section("F) إلغاء العميل قبل الرد + ضغطة قبول قديمة")
    b0 = await bal(U)  # noqa: F405
    oid_f, _ = await buy(u, cid)
    out = await u.click(f"tgp:cancel_yes:{oid_f}")
    expect("ألغى العميل" in texts_to(out, OWNER), "medium", "cancel-note", "صاحب القناة لم يُبلَّغ بالإلغاء")
    await ow.click(f"mp:o:{oid_f}:acc", expect_ok=False)
    out = await ow.click(f"mp:o:{oid_f}:t:now", expect_ok=False)
    expect((await order_row(oid_f))["status"] == "cancelled" and not FAKE[CH_MAIN]["msgs"] and await bal(U) == b0,  # noqa: F405
           "critical", "cancel-then-accept", "طلب مُلغى نُشر أو خُصم")

    # ═══ G حذف مبكر ═══
    section("G) صاحب القناة يحذف المنشور مبكراً")
    b0 = await bal(U)  # noqa: F405
    held0 = (await MP.earnings(OWNER))["held"]
    oid_g, _ = await buy(u, cid)
    await drive(ow, [f"cb:mp:o:{oid_g}:acc", f"cb:mp:o:{oid_g}:t:now"])
    o = await order_row(oid_g)
    FAKE[CH_MAIN]["msgs"].pop(o["channel_msg_ids"][0], None)    # الحذف اليدوي
    await age(oid_g, verified_at="4 hours")
    out = await tick(env)
    o = await order_row(oid_g)
    expect(o["status"] == "rejected" and await bal(U) == b0, "critical", "early-refund", f"الحذف المبكر: {o['status']}")  # noqa: F405
    expect((await MP.earnings(OWNER))["held"] == held0 and (await PC.get(cid))["early_n"] == 1, "critical", "early-no-earn", "ربح لمنشور محذوف مبكراً")
    expect("حُذف من" in texts_to(out, OWNER), "medium", "early-note", "لا إشعار لصاحب القناة")
    await invariants("G")

    # ═══ H موعد لاحق ═══
    section("H) موعد لاحق (بعد 3 ساعات)")
    oid_h, _ = await buy(u, cid, when="غداً 20:00")
    out = await ow.click(f"mp:o:{oid_h}:acc")
    expect("غداً 20:00" in texts_to(out, OWNER), "medium", "pref-when", "موعد العميل المفضل لا يظهر لصاحب القناة")
    out = await ow.click(f"mp:o:{oid_h}:t:3")
    o = await order_row(oid_h)
    expect(o["status"] == "in_progress" and not FAKE[CH_MAIN]["msgs"], "critical", "scheduled", "نُشر قبل الموعد")
    expect("تأكد الموعد" in texts_to(out, U), "medium", "customer-scheduled", "العميل لم يُبلَّغ بالموعد")  # noqa: F405
    await tick(env)
    expect((await order_row(oid_h))["status"] == "in_progress", "critical", "scheduled2", "نُشر قبل الموعد (الدورة)")
    await age(oid_h, scheduled_at="1 minute")
    await tick(env)
    expect((await order_row(oid_h))["status"] == "active" and len(FAKE[CH_MAIN]["msgs"]) == 1, "critical", "scheduled-publish", "لم يُنشر في موعده")
    # وقت مكتوب
    oid_h2, _ = await buy(u, cid)
    await drive(ow, [f"cb:mp:o:{oid_h2}:acc", f"cb:mp:o:{oid_h2}:t:x", "t:وقت غريب"])
    expect("ما فهمت" in " ".join(ow.last_texts), "medium", "bad-time", "موعد غير مفهوم قُبل")
    await drive(ow, ["t:بعد 2 ساعة"])
    expect((await order_row(oid_h2))["status"] == "in_progress", "high", "typed-time", "الموعد المكتوب لم يُقبل")
    await drive(ow, [f"cb:mp:o:{oid_h2}:now"])
    expect((await order_row(oid_h2))["status"] == "active", "high", "publish-now", "«انشر الآن» لم يعمل")

    # ═══ I فشل النشر ═══
    section("I) فشل النشر (سُحبت صلاحية النشر) ← إعادة ← استرداد")
    b0 = await bal(U)  # noqa: F405
    oid_i, _ = await buy(u, cid)
    FAKE[CH_MAIN]["bot"]["post"] = False
    out = await ow.click(f"mp:o:{oid_i}:acc")
    out = await ow.click(f"mp:o:{oid_i}:t:now")
    o = await order_row(oid_i)
    expect(o["status"] == "in_progress" and o["publish_attempts"] == 1, "critical", "publish-retry", f"فشل النشر: {o['status']}")
    expect("تعذّر على البوت النشر" in texts_to(out, OWNER), "medium", "fail-note", "صاحب القناة لم يُبلَّغ بفشل النشر")
    await tick(env)   # ما زال ضمن الحجز 5 دقائق
    await age(oid_i, publishing_until="1 minute")
    out = await tick(env)
    expect("تعذّر على البوت النشر" not in texts_to(out, OWNER), "low", "fail-spam", "تنبيه الفشل تكرر")
    await age(oid_i, publishing_until="1 minute", scheduled_at="3 hours")
    await tick(env)
    o = await order_row(oid_i)
    expect(o["status"] == "rejected" and await bal(U) == b0, "critical", "publish-refund", f"بعد المهلة: {o['status']}")  # noqa: F405
    FAKE[CH_MAIN]["bot"]["post"] = True
    await invariants("I")

    # ═══ J إزالة البوت ═══
    section("J) إزالة البوت من القناة وفيها طلبات جارية")
    b0 = await bal(U)  # noqa: F405
    oid_j1, _ = await buy(u, cid)                       # بانتظار الرد
    active_before = await qv("SELECT count(*) FROM orders WHERE channel_chat_id=$1 AND status='active'", CH_MAIN)  # noqa: F405
    paid_active = await qv("SELECT COALESCE(sum(price_usd),0) FROM orders WHERE channel_chat_id=$1 AND status='active'", CH_MAIN)  # noqa: F405
    out = await chat_member_update(env, CH_MAIN, OWNER, True, False)
    FAKE[CH_MAIN]["msgs"].clear()   # البوت خارج القناة: لم يعد يرى رسائله
    ch = await PC.get(cid)
    left = await qv("SELECT count(*) FROM orders WHERE channel_chat_id=$1 AND status IN ('submitted','in_progress','active')", CH_MAIN)  # noqa: F405
    expect(ch["mp_status"] == "suspended" and not ch["enabled"] and left == 0, "critical", "bot-removed",
           f"بعد إزالة البوت: {ch['mp_status']} enabled={ch['enabled']} جارية={left}")
    expect(await bal(U) == b0 + paid_active, "critical", "removed-refund",  # noqa: F405  (b0 قبل شراء j1 ← استرداده يعيدنا إليه)
           f"الاسترداد عند الإزالة: {await bal(U)} vs {b0}+…")  # noqa: F405
    expect("أُزيل البوت" in texts_to(out, OWNER), "medium", "removed-note", "صاحب القناة لم يُبلَّغ")
    print(f"    استُرد {left == 0 and (1 + active_before)} طلب · القناة موقوفة")
    await invariants("J")
    # إعادة البوت + إعادة التفعيل من الأدمن
    out = await chat_member_update(env, CH_MAIN, OWNER, False, True)
    expect("مسجّلة عندك" in texts_to(out, OWNER), "medium", "re-add", "إعادة إضافة البوت لم تعرض القناة")
    await a.click(f"adm:mp:res:{cid}")
    expect((await PC.get(cid))["mp_status"] == "approved", "high", "resume", "لم تُعد القناة")

    # ═══ K البلاغات ═══
    section("K) البلاغات: مقبول ثم مرفوض")
    async def complete_one():
        oid_, _ = await buy(u, cid)
        await drive(ow, [f"cb:mp:o:{oid_}:acc", f"cb:mp:o:{oid_}:t:now"])
        await age(oid_, ends_at="1 minute")
        await tick(env)
        return oid_
    oid_k1 = await complete_one()
    held0 = (await MP.earnings(OWNER))["held"]
    b0 = await bal(U)  # noqa: F405
    await u.click(f"ord:view:{oid_k1}")
    expect(u.find_cb(f"mpc:dsp:{oid_k1}") is not None, "high", "dsp-btn", "لا زر بلاغ في الطلب المكتمل")
    out = await drive(u, [f"cb:mpc:dsp:{oid_k1}", f"cb:mpc:dspok:{oid_k1}"])
    o = await order_row(oid_k1)
    expect(o["payout_status"] == "disputed", "critical", "dispute-open", f"البلاغ لم يُفتح: {o['payout_status']}")
    await age(oid_k1, payout_at="1 minute")
    await tick(env)
    expect((await order_row(oid_k1))["payout_status"] == "disputed", "critical", "dispute-release", "رُبح محل بلاغ تحرّر")
    await a.click(f"adm:mp:dspr:{oid_k1}")
    o = await order_row(oid_k1)
    e = await MP.earnings(OWNER)
    expect(o["status"] == "refunded" and o["payout_status"] == "reversed" and await bal(U) == b0 + o["price_usd"]  # noqa: F405
           and e["held"] == held0 - D("10"), "critical", "dispute-refund", f"البلاغ المقبول: {o['status']}/{o['payout_status']} held={e['held']}")
    out = await a.click(f"adm:mp:dspr:{oid_k1}", expect_ok=False)
    expect(await bal(U) == b0 + o["price_usd"], "critical", "dispute-double", "استرداد مزدوج بضغطتين")  # noqa: F405
    oid_k2 = await complete_one()
    await drive(u, [f"cb:mpc:dsp:{oid_k2}", f"cb:mpc:dspok:{oid_k2}"])
    await a.click(f"adm:mp:dspk:{oid_k2}")
    await age(oid_k2, payout_at="1 minute")
    await tick(env)
    expect((await order_row(oid_k2))["payout_status"] == "available", "critical", "dispute-kept", "بعد رفض البلاغ لم يتحرر الربح")
    out = await u.click(f"mpc:dspok:{oid_k2}", expect_ok=False)
    expect("انتهت" in alerts(out), "high", "late-dispute", "بلاغ بعد التحرير قُبل")
    await invariants("K")

    # ═══ L السحب ═══
    section("L) السحب")
    e = await MP.earnings(OWNER)
    print(f"    المتاح قبل السحب: {e['avail']}")
    await drive(ow, ["cb:mp:earn", "cb:mp:wd", "cb:mp:wd:m:usdt_trc20", "t:not-an-address"])
    expect("TRC20" in " ".join(ow.last_texts), "high", "addr-validate", "عنوان خاطئ قُبل")
    addr = "T" + "A" * 33
    await drive(ow, [f"t:{addr}", "cb:mp:wd:amt", "t:5"])
    out = await ow.click("mp:wd:ok", expect_ok=False)
    expect("الحد الأدنى" in alerts(out), "high", "min-payout", "سحب أقل من الحد قُبل")
    await drive(ow, ["cb:mp:wd:amt", "t:12"])
    out = await ow.click("mp:wd:ok")
    p = await q1("SELECT * FROM payouts WHERE user_id=$1 ORDER BY id DESC LIMIT 1", OWNER)  # noqa: F405
    e2 = await MP.earnings(OWNER)
    expect(p and p["status"] == "pending" and p["amount_usd"] == D("12") and e2["avail"] == e["avail"] - 12, "critical", "payout-req",
           f"طلب السحب: {dict(p) if p else None} avail={e2['avail']}")
    expect(any("طلب سحب PAY-" in (d.get("text") or "") for n, d in out), "high", "payout-card", "لم تصل بطاقة السحب للأدمن")
    out = await ow.click("mp:wd", expect_ok=False)
    expect("قيد التنفيذ" in alerts(out), "high", "second-payout", "سحب ثانٍ أثناء وجود معلّق")
    await drive(a, [f"cb:adm:mp:paid:{p['id']}", "t:TX123abc"])
    out_p = await q1("SELECT status, note FROM payouts WHERE id=$1", p["id"])  # noqa: F405
    expect(out_p["status"] == "paid" and out_p["note"] == "TX123abc", "critical", "payout-paid", f"الدفع: {dict(out_p)}")
    await a.click(f"adm:mp:paid:{p['id']}")
    out = await a.text("again")
    expect("حُسم مسبقاً" in texts_to(out, A), "high", "payout-double", "دفع مزدوج")  # noqa: F405
    # سحب يُرفض ويعود المبلغ — المتاح 8$ < الحد الأدنى: يُرفض أولاً
    out = await ow.click("mp:wd", expect_ok=False)
    expect("الحد الأدنى" in alerts(out), "high", "min-avail", "سحب مسموح والمتاح أقل من الحد")
    await db.execute("UPDATE users SET earn_avail_usd = earn_avail_usd + 10 WHERE tg_id=$1", OWNER)
    await db.execute("INSERT INTO earnings_ledger (user_id, bucket, type, amount_usd, note) VALUES ($1,'avail','adjust',10,'sim')", OWNER)
    await drive(ow, ["cb:mp:wd", "cb:mp:wd:m:shamcash", "t:0933 123 456", "cb:mp:wd:amt", "t:10", "cb:mp:wd:ok"])
    p2 = await q1("SELECT * FROM payouts WHERE user_id=$1 ORDER BY id DESC LIMIT 1", OWNER)  # noqa: F405
    expect(p2["id"] != p["id"] and p2["status"] == "pending" and p2["address"] == "0933 123 456", "critical", "payout2", f"طلب شام كاش لم يُنشأ: {dict(p2)}")
    av = (await MP.earnings(OWNER))["avail"]
    await drive(a, [f"cb:adm:mp:payno:{p2['id']}", "t:الرقم غير صحيح"])
    expect((await MP.earnings(OWNER))["avail"] == av + 10, "critical", "payout-return", "السحب المرفوض لم يُرجَع")
    await invariants("L")

    # ═══ M التحويل ═══
    section("M) تحويل الأرباح إلى رصيد إعلانات")
    av = (await MP.earnings(OWNER))["avail"]
    b0 = await bal(OWNER)  # noqa: F405
    await drive(ow, ["cb:mp:earn", "cb:mp:cv", "cb:mp:cv:amt", "t:3", "cb:mp:cv:ok"])
    expect(await bal(OWNER) == b0 + 3 and (await MP.earnings(OWNER))["avail"] == av - 3, "critical", "convert",  # noqa: F405
           f"التحويل: رصيد {await bal(OWNER)} متاح {(await MP.earnings(OWNER))['avail']}")  # noqa: F405
    out = await ow.click("mp:cv:ok", expect_ok=False)   # يحوّل الباقي كله (الجلسة مُسحت) — مقبول ومتسق
    await invariants("M")

    # ═══ N التسابقات ═══
    section("N) تسابقات")
    # نشر مزدوج متزامن
    oid_n, _ = await buy(u, cid)
    await MP.owner_accept(oid_n, OWNER, MP.now())
    r = await asyncio.gather(MP.publish_one(env.bot, oid_n), MP.publish_one(env.bot, oid_n), MP.publish_one(env.bot, oid_n))
    pubs = sum(1 for x, _ in r if x == "published")
    o = await order_row(oid_n)
    mine = [m for m in FAKE[CH_MAIN]["msgs"] if m in (o["channel_msg_ids"] or [])]
    expect(pubs == 1 and len(mine) == 1 and len(o["channel_msg_ids"]) == 1, "critical", "race-publish", f"نشر متزامن ×3 ← نُشر {pubs} مرة، رسائل الطلب {len(mine)}")
    # قبول وإلغاء متزامنان
    for i in range(5):
        oid_r, _ = await buy(u, cid)
        b1 = await bal(U)  # noqa: F405
        res = await asyncio.gather(u.click(f"tgp:cancel_yes:{oid_r}", label=f"إلغاء {i}"),
                                   ow.click(f"mp:o:{oid_r}:t:now", label=f"قبول {i}", expect_ok=False), return_exceptions=True)
        o = await order_row(oid_r)
        on_channel = [m for m in FAKE[CH_MAIN]["msgs"] if m in (o["channel_msg_ids"] or [])]
        ok = (o["status"] == "cancelled" and not on_channel and await bal(U) == b1 + o["price_usd"]) or \
             (o["status"] == "active" and len(on_channel) == 1 and await bal(U) == b1)  # noqa: F405
        expect(ok, "critical", "race-cancel-accept", f"#{oid_r}: {o['status']} منشورات={len(on_channel)} رصيد={await bal(U)} قبل={b1}")  # noqa: F405
        del res
    # تحويل مزدوج + سحبان متزامنان
    await db.execute("UPDATE users SET earn_avail_usd = earn_avail_usd + 30 WHERE tg_id=$1", OWNER)
    await db.execute("INSERT INTO earnings_ledger (user_id, bucket, type, amount_usd, note) VALUES ($1,'avail','adjust',30,'sim')", OWNER)
    av = (await MP.earnings(OWNER))["avail"]
    b0 = await bal(OWNER)  # noqa: F405
    r = await asyncio.gather(*[MP.convert_to_balance(OWNER, av) for _ in range(4)], return_exceptions=True)
    okc = sum(1 for x in r if not isinstance(x, Exception))
    expect(okc == 1 and await bal(OWNER) == b0 + av and (await MP.earnings(OWNER))["avail"] == 0, "critical", "race-convert",  # noqa: F405
           f"تحويل ×4 متزامن ← نجح {okc}")
    await db.execute("UPDATE users SET earn_avail_usd = earn_avail_usd + 40 WHERE tg_id=$1", OWNER)
    await db.execute("INSERT INTO earnings_ledger (user_id, bucket, type, amount_usd, note) VALUES ($1,'avail','adjust',40,'sim')", OWNER)
    r = await asyncio.gather(*[MP.request_payout(OWNER, D("15"), "usdt_trc20", addr) for _ in range(4)], return_exceptions=True)
    okp = sum(1 for x in r if not isinstance(x, Exception))
    expect(okp == 1 and (await MP.earnings(OWNER))["avail"] == 25, "critical", "race-payout", f"سحب ×4 متزامن ← نجح {okp}")
    # تحرير مزدوج
    oid_rel = await complete_one()
    await age(oid_rel, payout_at="1 minute")
    held0, av0 = (await MP.earnings(OWNER))["held"], (await MP.earnings(OWNER))["avail"]
    r = await asyncio.gather(*[MP.release_one(oid_rel) for _ in range(4)])
    e = await MP.earnings(OWNER)
    expect(sum(1 for x in r if x) == 1 and e["avail"] == av0 + 10 and e["held"] == held0 - 10, "critical", "race-release",
           f"تحرير ×4 ← {sum(1 for x in r if x)}")
    # نافذة التسابق محتومة: صاحب القناة قرأ الطلب «بانتظار ردك» ثم ألغاه العميل قبل أن يُكتب القبول
    for act in ("accept", "reject"):
        oid_w, _ = await buy(u, cid)
        stale = await order_row(oid_w)
        await u.click(f"tgp:cancel_yes:{oid_w}")
        b1 = await bal(U)  # noqa: F405
        real = MP.owner_order
        async def _stale(order_id, owner_id, _s=stale):
            return dict(_s)
        MP.owner_order = _stale
        try:
            if act == "accept":
                await MP.owner_accept(oid_w, OWNER, MP.now())
            else:
                await MP.owner_reject(oid_w, OWNER, "aud")
            got = "نُفِّذ"
        except MPError:
            got = "رُفض"
        finally:
            MP.owner_order = real
        await tick(env)
        o = await order_row(oid_w)
        expect(got == "رُفض" and o["status"] == "cancelled" and await bal(U) == b1 and  # noqa: F405
               not [m for m in FAKE[CH_MAIN]["msgs"] if m in (o["channel_msg_ids"] or [])],
               "critical", f"stale-{act}", f"{act} بلقطة قديمة بعد الإلغاء: {got} ← {o['status']} رصيد {await bal(U)} vs {b1}")  # noqa: F405
    # حجز الربح مرتين لنفس الطلب (استدعاء مزدوج متزامن لـ on_completed) — مرة واحدة فقط
    o_done = await order_row(oid_rel)
    held0 = (await MP.earnings(OWNER))["held"]
    r = await asyncio.gather(*[MP.on_completed(o_done) for _ in range(3)])
    expect(not any(r) and (await MP.earnings(OWNER))["held"] == held0, "critical", "earn-twice",
           f"on_completed ×3 لطلب محسوب مسبقاً ← {r}")
    # إنهاء مكرر عبر PP.finish (أدمن) بعد الإنهاء التلقائي — لا ربح مكرر
    from app.services import partner_posts as PP
    await PP.finish(oid_rel, A)  # noqa: F405
    await invariants("N")

    # ═══ O «اكتبولي» ═══
    section("O) إضافة «اكتبولي»: الطلب يصل صاحب القناة بعد أن يكتب الفريق النص")
    oid_o, out = await buy(u, cid, copy=True)
    o = await order_row(oid_o)
    expect(o["owner_user_id"] == OWNER and o["owner_deadline"] is None and "طلب إعلان جديد" not in texts_to(out, OWNER),
           "critical", "copy-wait", f"وصل صاحب القناة قبل النص: dl={o['owner_deadline']}")
    out = await ow.click(f"mp:o:{oid_o}:t:now", expect_ok=False)
    expect((await order_row(oid_o))["status"] == "submitted", "critical", "copy-accept", "قُبل طلب بلا نص")
    await drive(a, [f"cb:adm:tgp:{oid_o}:text"])
    out = await a.text("نص كتبه فريق ترويج باحتراف ✨")
    o = await order_row(oid_o)
    expect(o["owner_deadline"] is not None and "طلب إعلان جديد" in texts_to(out, OWNER), "critical", "copy-dispatch",
           "بعد كتابة النص لم يصل الطلب لصاحب القناة")
    await drive(ow, [f"cb:mp:o:{oid_o}:acc", f"cb:mp:o:{oid_o}:t:now"])
    expect((await order_row(oid_o))["status"] == "active", "high", "copy-publish", "لم يُنشر نص الفريق")

    # ═══ P رفض قناة ثم إعادة إرسال ═══
    section("P) قناة ثانية بلا صلاحية تثبيت ← رفض الأدمن ← تعديل وإعادة إرسال")
    await drive(ow2, ["cb:mp:add"])
    out = await ow2.text("t.me/aleppo_tech")
    t = texts_to(out, OWNER2)
    expect("التثبيت غير متاح" in t, "medium", "no-pin-note", f"لم يُنبَّه لغياب صلاحية التثبيت: {t[:120]}")
    cid2 = int(find_btn(out, OWNER2, "mp:cat:").split(":")[2])
    await drive(ow2, [f"cb:mp:cat:{cid2}:tech", "t:4", "cb:mp:p48skip", "t:6"])
    ch2 = await PC.get(cid2)
    expect(ch2["price_pin"] is None and not ch2["allow_pin"], "critical", "pin-no-rights", "تثبيت مسموح بلا صلاحية للبوت")
    await drive(ow2, ["cb:mp:blurbskip", f"cb:mp:submit:{cid2}"])
    await drive(a, [f"cb:adm:mp:no:{cid2}", "t:عدد المشاهدات ضعيف"])
    ch2 = await PC.get(cid2)
    expect(ch2["mp_status"] == "rejected" and "ضعيف" in ch2["mp_note"], "high", "ch-reject", "الرفض لم يُحفظ")
    await drive(ow2, ["cb:mp:home", f"cb:mp:ch:{cid2}"])
    expect("مرفوضة" in " ".join(ow2.last_texts), "medium", "reject-visible", "صاحب القناة لا يرى سبب الرفض")
    await drive(ow2, [f"cb:mp:prices:{cid2}", "t:3", "cb:mp:p48skip", "cb:mp:pinskip", f"cb:mp:submit:{cid2}"])
    expect((await PC.get(cid2))["mp_status"] == "pending", "high", "resubmit", "إعادة الإرسال لم تعمل")
    await a.click(f"adm:mp:ok:{cid2}")
    # OWNER مشرف في CH_B: مسجّلة من حساب آخر
    await drive(ow, ["cb:mp:add"])
    out = await ow.text("@aleppo_tech")
    expect("حساب آخر" in texts_to(out, OWNER), "critical", "admin-steal", "مشرف سجّل قناة مسجّلة لمالكها")

    # ═══ Q إيقاف/إعادة ═══
    section("Q) إيقاف مؤقت من المالك + إيقاف من الأدمن")
    await ow2.click(f"mp:toggle:{cid2}")
    expect(cid2 not in [c["id"] for c in await PC.list_live()], "critical", "owner-pause", "القناة الموقوفة ظاهرة")
    await ow2.click(f"mp:toggle:{cid2}")
    await drive(a, [f"cb:adm:mp:sus:{cid2}", "t:شكاوى متكررة"])
    expect(cid2 not in [c["id"] for c in await PC.list_live()], "critical", "admin-suspend", "القناة الموقوفة إدارياً ظاهرة")
    out = await ow2.click(f"mp:toggle:{cid2}", expect_ok=False)
    expect("الإدارة" in alerts(out), "high", "owner-unsuspend", "المالك أعاد قناة أوقفتها الإدارة")
    await a.click(f"adm:mp:res:{cid2}")

    # ═══ R الإعدادات ═══
    section("R) إعدادات السوق من البوت")
    await drive(a, ["cb:adm:mp", "cb:adm:mp:cfg", "cb:adm:mp:set:hold_hours", "t:999"])
    expect((await MP.cfg())["hold_hours"] == 48, "high", "cfg-limit", "قيمة خارج الحدود قُبلت")
    await drive(a, ["cb:adm:mp:set:hold_hours", "t:24"])
    expect((await MP.cfg())["hold_hours"] == 24, "high", "cfg-save", "الإعداد لم يُحفظ")
    await drive(a, ["cb:adm:mp:onoff"])
    out = await st.click("mp:home", expect_ok=False)
    expect("متوقف" in alerts(out), "medium", "mp-off", "السوق المتوقف ما زال يقبل التسجيل")
    await drive(a, ["cb:adm:mp:onoff", "cb:adm:mp:list:all", "cb:adm:mp:pays", "cb:adm:mp:dsps", f"cb:adm:mp:ch:{cid}"])

    # ═══ S الأمان ═══
    section("S) الأمان: أزرار طلبات/قنوات/أرباح الآخرين")
    oid_s, _ = await buy(u, cid)
    for data in (f"mp:o:{oid_s}", f"mp:o:{oid_s}:acc", f"mp:o:{oid_s}:t:now", f"mp:o:{oid_s}:r:aud", f"mp:ch:{cid}",
                 f"mp:prices:{cid}", f"mp:toggle:{cid}", f"mp:submit:{cid}", f"mp:del:{cid}", f"mp:cat:{cid}:news"):
        await st.click(data, label=f"غريب {data}", expect_ok=False)
    o = await order_row(oid_s)
    ch = await PC.get(cid)
    expect(o["status"] == "submitted" and ch["category"] == "shopping" and ch["enabled"], "critical", "idor",
           f"غريب غيّر طلب/قناة غيره: {o['status']} {ch['category']} {ch['enabled']}")
    for data in (f"mpc:rate:{oid}:1", f"mpc:dspok:{oid_k2}", f"mpc:dsp:{oid}"):
        await u2.click(data, label=f"عميل آخر {data}", expect_ok=False)
    expect((await PC.get(cid))["rating_n"] == 1, "critical", "idor-rate", "عميل قيّم طلب غيره")
    for data in ("adm:mp", f"adm:mp:ok:{cid2}", f"adm:mp:dspr:{oid_k2}", "adm:mp:set:min_payout"):
        await st.click(data, label=f"غريب {data}", expect_ok=False)
    expect((await PC.get(cid2))["mp_status"] == "approved", "critical", "idor-admin", "غير الأدمن نفّذ إجراء إدارة")
    await drive(ow, [f"cb:mp:o:{oid_s}:rej", f"cb:mp:o:{oid_s}:r:time"])
    # تصفح شاشات صاحب القناة كلها
    await drive(ow, ["cb:mp:home", "cb:mp:orders", "cb:mp:earn", "cb:mp:hist", "cb:mp:how", f"cb:mp:ch:{cid}", f"cb:mp:resync:{cid}"])

    # ═══ Z نهاية: إنهاء كل شيء + ثوابت ═══
    section("Z) إنهاء كل المنشورات الجارية + الثوابت")
    await db.execute("UPDATE orders SET ends_at = now() - interval '1 minute' WHERE status = 'active' AND owner_user_id IS NOT NULL")
    await db.execute("UPDATE orders SET owner_deadline = now() - interval '1 minute' WHERE status = 'submitted' AND owner_user_id IS NOT NULL AND owner_deadline IS NOT NULL")
    await tick(env)
    await db.execute("UPDATE orders SET payout_at = now() - interval '1 minute' WHERE payout_status = 'held'")
    await tick(env)
    await invariants("Z")
    stats = await q1("SELECT count(*) FILTER (WHERE status='completed') c, count(*) FILTER (WHERE status IN ('rejected','cancelled','refunded')) r, "  # noqa: F405
                     "count(*) FILTER (WHERE status IN ('submitted','in_progress','active')) o FROM orders WHERE owner_user_id IS NOT NULL")
    e = await MP.earnings(OWNER)
    print(f"    طلبات السوق: {stats['c']} مكتمل · {stats['r']} مُسترد/ملغى · {stats['o']} مفتوح (نسخ «اكتبولي»)")
    print(f"    أرباح {OWNER}: متاح {e['avail']} · محجوز {e['held']} · مسحوب {e['paid']} · محوّل {e['converted']}")

    report(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim_marketplace.json"), "محاكاة سوق القنوات")  # noqa: F405
    await env.close()
    serious = [i for i in ISSUES.items if i.sev in ("critical", "high")]  # noqa: F405
    return 1 if serious else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
