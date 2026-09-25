"""💼 سوق القنوات ذاتي الخدمة — المنطق كله هنا (بلا واجهة).

الأطراف: صاحب القناة (owner) · العميل (customer) · الأدمن.

دورة القناة (partner_channels.mp_status):
    draft ← يكمل الفئة/الأسعار ← pending (بانتظار الأدمن) ← approved (ظاهرة للعملاء)
                                                      ↘ rejected (يمكن التعديل وإعادة الإرسال)
    approved ⇄ suspended (الأدمن، أو تلقائياً عند إزالة البوت من القناة)
    approved + enabled=FALSE = أوقفها صاحبها مؤقتاً.

دورة الطلب (orders.status لطلب tg_post في قناة سوق):
    submitted (بانتظار صاحب القناة حتى owner_deadline) ── رفض/انتهاء المهلة/إلغاء العميل ──→ استرداد كامل
       ↓ قبول + موعد
    in_progress ── في الموعد: البوت ينشر في القناة (حجز publishing_until يمنع النشر المزدوج)
       ↓
    active ── تحقق دوري من وجود المنشور؛ حُذف مبكراً ← استرداد كامل للعميل (early_n++)
       ↓ ends_at
    completed ── البوت يحذف المنشور؛ ربح صاحب القناة (cost_usd) يُحجز held
       ↓ بعد hold_hours بلا بلاغ
    الربح available ← سحب (USDT/شام كاش، يعتمده الأدمن) أو تحويل إلى رصيد إعلانات.

قواعد المال (نفس قواعد money.py):
    - كل تغيير في أرباح صاحب القناة = سطر في earnings_ledger + تحديث users.earn_* في معاملة واحدة.
    - lock_user أولاً في كل معاملة، ومفاتيح «مرة واحدة» (claim_key) لقيد الربح.
    - العميل لا يُسترد له بعد تحرير الربح؛ البلاغ متاح فقط والربح محجوز (held).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.db import pool as db
from app.db.repo import events, orders as repo, partner_channels as PC, settings as settings_repo
from app.services import money as money_svc
from app.services.pricing import money

log = logging.getLogger(__name__)


class MPError(Exception):
    """خطأ يُعرض لصاحب القناة/العميل كما هو (نص عربي واضح)."""


DEFAULTS = {
    "enabled": True,
    "accept_hours": 12,      # مهلة قبول/رفض صاحب القناة
    "hold_hours": 48,        # حجز الربح بعد انتهاء المنشور (نافذة بلاغات العميل)
    "min_payout": 10,        # الحد الأدنى للسحب بالدولار
    "verify_hours": 3,       # كل كم ساعة نتحقق أن المنشور ما زال موجوداً
    "publish_grace_hours": 2,  # إن تعذّر النشر كل هذه المدة بعد الموعد ← استرداد
}
CFG_LIMITS = {"accept_hours": (1, 72), "hold_hours": (0, 240), "min_payout": (1, 1000), "verify_hours": (1, 24),
              "publish_grace_hours": (1, 24)}

PAYOUT_METHODS = {"usdt_trc20": "💵 USDT (TRC20)", "usdt_bep20": "💵 USDT (BEP20)", "shamcash": "📱 شام كاش"}
REJECT_REASONS = {
    "aud": "المحتوى لا يناسب جمهور القناة",
    "time": "لا يوجد موعد متاح قريباً",
    "rules": "المحتوى مخالف لسياسة القناة",
}
PRICE_MIN, PRICE_MAX = Decimal("0.5"), Decimal("5000")


async def cfg() -> dict:
    val = await settings_repo.get("marketplace", {}) or {}
    return {**DEFAULTS, **{k: v for k, v in val.items() if k in DEFAULTS}}


async def set_cfg(key: str, value) -> dict:
    if key not in CFG_LIMITS and key != "enabled":
        raise MPError("إعداد غير معروف")
    cur = await settings_repo.get("marketplace", {}) or {}
    if key == "enabled":
        cur[key] = bool(value)
    else:
        lo, hi = CFG_LIMITS[key]
        try:
            v = int(str(value).strip())
        except ValueError:
            raise MPError("اكتب رقماً صحيحاً") from None
        if not lo <= v <= hi:
            raise MPError(f"القيمة بين {lo} و {hi}")
        cur[key] = v
    await settings_repo.set_("marketplace", cur)
    return await cfg()


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_price(raw, required: bool = True) -> Decimal | None:
    s = str(raw or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).replace("،", ".").replace(",", ".")
    s = s.replace("$", "").replace("دولار", "").strip()
    if not s:
        if required:
            raise MPError("اكتب السعر رقماً بالدولار، مثل: 10")
        return None
    try:
        d = money(Decimal(s))
    except (InvalidOperation, ValueError):
        raise MPError("السعر رقم بالدولار فقط، مثل: 10 أو 7.5") from None
    if not PRICE_MIN <= d <= PRICE_MAX:
        raise MPError(f"السعر بين {PRICE_MIN}$ و {PRICE_MAX}$")
    return d


# ═════════════════════════ القناة: التحقق والتسجيل ═════════════════════════

def is_mp_channel(ch: dict | None) -> bool:
    return bool(ch and ch.get("owner_user_id") and ch.get("chat_id") and ch.get("mp_status") in ("approved", "suspended"))


_REF_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,31})/?$|^@?([A-Za-z][A-Za-z0-9_]{3,31})$", re.I)


def channel_ref(raw: str) -> str | None:
    """«@name» · «t.me/name» → «@name». روابط الدعوة (t.me/+…) = قناة خاصة → None."""
    m = _REF_RE.match((raw or "").strip())
    if not m:
        return None
    return "@" + (m.group(1) or m.group(2))


async def verify_channel(bot, ref, user_id: int) -> dict:
    """يتحقق فعلياً من تيليغرام: قناة عامة · البوت مشرف بصلاحيات النشر والحذف · المستخدم مالكها/مشرف فيها.

    يعيد {chat_id, title, username, url, subscribers, can_pin}. يرمي MPError بنص واضح."""
    try:
        chat = await bot.get_chat(ref)
    except Exception:  # noqa: BLE001
        raise MPError("ما قدرت أوصل للقناة 🤔 تأكد من المعرّف، وأن البوت مضاف فيها مشرفاً.") from None
    if chat.type != "channel":
        raise MPError("هذا ليس قناة — السوق للقنوات فقط 📢")
    if not chat.username:
        raise MPError("القناة خاصة 🔒 — السوق للقنوات العامة (لها @معرّف) حتى يستطيع العملاء تصفحها.")
    me = await bot.me()
    try:
        bm = await bot.get_chat_member(chat.id, me.id)
    except Exception:  # noqa: BLE001
        bm = None
    if not bm or bm.status != "administrator" or not getattr(bm, "can_post_messages", False) \
            or not getattr(bm, "can_delete_messages", False):
        raise MPError("البوت ليس مشرفاً في القناة بصلاحيتي «نشر الرسائل» و«حذف الرسائل». "
                      "أضفه من زر «➕ أضف البوت لقناتي» ثم أعد المحاولة.")
    try:
        um = await bot.get_chat_member(chat.id, user_id)
    except Exception:  # noqa: BLE001
        um = None
    if not um or um.status not in ("creator", "administrator"):
        raise MPError("حسابك ليس مالك هذه القناة ولا مشرفاً فيها — التسجيل لأصحاب القنوات فقط.")
    try:
        subs = int(await bot.get_chat_member_count(chat.id))
    except Exception:  # noqa: BLE001
        subs = 0
    return {
        "chat_id": int(chat.id), "title": " ".join((chat.title or chat.username).split())[:40], "username": chat.username,
        "url": f"https://t.me/{chat.username}", "subscribers": subs, "can_pin": bool(getattr(bm, "can_edit_messages", False)),
    }


async def channel_by_chat(chat_id: int) -> dict | None:
    return PC._d(await db.fetchrow(
        "SELECT * FROM partner_channels WHERE chat_id = $1 AND NOT archived "
        "AND mp_status IN ('draft','pending','approved','suspended','rejected') ORDER BY id DESC LIMIT 1", chat_id))


async def create_draft(owner_id: int, info: dict) -> tuple[dict, bool]:
    """ينشئ مسودة قناة (أو يعيد الموجودة). يعيد (القناة، جديدة؟). قناة مسجّلة لحساب آخر → MPError."""
    ex = await channel_by_chat(info["chat_id"])
    if ex and ex["owner_user_id"] != owner_id and ex["mp_status"] != "rejected":
        raise MPError("هذه القناة مسجّلة في السوق من حساب آخر. إن كانت قناتك راسل الدعم.")
    if ex and ex["owner_user_id"] == owner_id:
        row = await db.fetchrow(
            "UPDATE partner_channels SET title = $2, username = $3, url = $4, subscribers = $5, verified_at = now(), "
            "subs_checked_at = now(), allow_pin = CASE WHEN $6 THEN allow_pin ELSE FALSE END, updated_at = now() "
            "WHERE id = $1 RETURNING *", ex["id"], info["title"], info["username"], info["url"], info["subscribers"], info["can_pin"])
        return PC._d(row), False
    if ex:   # مرفوضة لحساب آخر — تُؤرشف القديمة ويبدأ المالك الحقيقي من جديد
        await db.execute("UPDATE partner_channels SET archived = TRUE, enabled = FALSE, updated_at = now() WHERE id = $1", ex["id"])
    row = await db.fetchrow(
        "INSERT INTO partner_channels (title, username, url, category, subscribers, blurb, price_24h, allow_pin, owner_contact, "
        "notes, enabled, sort_order, owner_user_id, chat_id, mp_status, verified_at, subs_checked_at) "
        "VALUES ($1, $2, $3, 'general', $4, '', 0, $5, $6, 'سجّلها صاحبها من البوت', FALSE, 100, $7, $8, 'draft', now(), now()) "
        "RETURNING *",
        info["title"], info["username"], info["url"], info["subscribers"], info["can_pin"], f"tg:{owner_id}", owner_id, info["chat_id"])
    ch = PC._d(row)
    await events.log_event("mp_channel_draft", owner_id, None, channel_id=ch["id"], chat_id=info["chat_id"], subs=info["subscribers"])
    return ch, True


async def owner_channel(channel_id: int, owner_id: int) -> dict:
    ch = await PC.get(channel_id)
    if not ch or ch.get("owner_user_id") != owner_id or ch.get("archived"):
        raise MPError("القناة غير موجودة")
    return ch


async def my_channels(owner_id: int) -> list[dict]:
    rows = await db.fetch("SELECT * FROM partner_channels WHERE owner_user_id = $1 AND NOT archived ORDER BY id", owner_id)
    return [PC._d(r) for r in rows]


async def update_channel(channel_id: int, owner_id: int, **fields) -> dict:
    await owner_channel(channel_id, owner_id)
    allowed = {"category", "price_24h", "price_48h", "price_pin", "allow_pin", "blurb"}
    cols = [k for k in fields if k in allowed]
    if not cols:
        return await PC.get(channel_id)
    if "category" in fields and fields["category"] not in PC.CATEGORIES:
        raise MPError("الفئة غير معروفة")
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(cols))
    row = await db.fetchrow(f"UPDATE partner_channels SET {sets}, updated_at = now() WHERE id = $1 RETURNING *",
                            channel_id, *[fields[k] for k in cols])
    return PC._d(row)


async def submit_for_review(channel_id: int, owner_id: int) -> dict:
    ch = await owner_channel(channel_id, owner_id)
    if Decimal(str(ch["price_24h"] or 0)) < PRICE_MIN:
        raise MPError("حدّد سعر منشور 24 ساعة أولاً")
    row = await db.fetchrow(
        "UPDATE partner_channels SET mp_status = 'pending', mp_note = '', updated_at = now() "
        "WHERE id = $1 AND mp_status IN ('draft','rejected') RETURNING *", channel_id)
    if not row:
        raise MPError("القناة أُرسلت للمراجعة مسبقاً")
    await events.log_event("mp_channel_submitted", owner_id, None, channel_id=channel_id)
    return PC._d(row)


async def owner_toggle(channel_id: int, owner_id: int) -> dict:
    """⏸️/▶️ صاحب القناة يوقفها مؤقتاً أو يعيدها (فقط إن كانت معتمدة)."""
    ch = await owner_channel(channel_id, owner_id)
    if ch["mp_status"] != "approved":
        raise MPError("القناة غير معتمدة بعد" if ch["mp_status"] != "suspended" else "القناة موقوفة من الإدارة")
    row = await db.fetchrow("UPDATE partner_channels SET enabled = NOT enabled, updated_at = now() "
                            "WHERE id = $1 AND mp_status = 'approved' RETURNING *", channel_id)
    return PC._d(row)


async def delete_draft(channel_id: int, owner_id: int) -> bool:
    ch = await owner_channel(channel_id, owner_id)
    if ch["mp_status"] not in ("draft", "rejected"):
        raise MPError("لا يمكن حذف قناة معتمدة أو قيد المراجعة من هنا — أوقفها مؤقتاً بدلاً من ذلك")
    return (await PC.delete_or_archive(channel_id)) in ("deleted", "archived")


async def resync_subs(bot, channel_id: int) -> dict:
    ch = await PC.get(channel_id)
    n = int(await bot.get_chat_member_count(ch["chat_id"]))
    row = await db.fetchrow("UPDATE partner_channels SET subscribers = $2, subs_checked_at = now(), updated_at = now() "
                            "WHERE id = $1 RETURNING *", channel_id, n)
    return PC._d(row)


# ——— الأدمن ———

async def admin_decide_channel(channel_id: int, admin_id: int, approve: bool, reason: str = "") -> dict | None:
    if approve:
        row = await db.fetchrow("UPDATE partner_channels SET mp_status = 'approved', enabled = TRUE, mp_note = '', updated_at = now() "
                                "WHERE id = $1 AND mp_status = 'pending' RETURNING *", channel_id)
    else:
        row = await db.fetchrow("UPDATE partner_channels SET mp_status = 'rejected', enabled = FALSE, mp_note = $2, updated_at = now() "
                                "WHERE id = $1 AND mp_status = 'pending' RETURNING *", channel_id, (reason or "")[:200])
    if row:
        await events.log_event("mp_channel_" + ("approved" if approve else "rejected"), row["owner_user_id"], None,
                               channel_id=channel_id, admin_id=admin_id, reason=reason)
    return PC._d(row)


async def admin_suspend(channel_id: int, admin_id: int, suspend: bool, reason: str = "") -> dict | None:
    if suspend:
        row = await db.fetchrow("UPDATE partner_channels SET mp_status = 'suspended', enabled = FALSE, mp_note = $2, updated_at = now() "
                                "WHERE id = $1 AND mp_status = 'approved' RETURNING *", channel_id, (reason or "أوقفتها الإدارة")[:200])
    else:
        row = await db.fetchrow("UPDATE partner_channels SET mp_status = 'approved', enabled = TRUE, mp_note = '', updated_at = now() "
                                "WHERE id = $1 AND mp_status = 'suspended' RETURNING *", channel_id)
    if row:
        await events.log_event("mp_channel_suspend" if suspend else "mp_channel_resume", row["owner_user_id"], None,
                               channel_id=channel_id, admin_id=admin_id)
    return PC._d(row)


async def on_bot_removed(chat_id: int) -> tuple[dict | None, list[dict]]:
    """أُزيل البوت من قناة سوق: توقف فوراً، وكل طلب لم يكتمل يُسترد للعميل (لا يمكن النشر ولا التحقق)."""
    ch = await channel_by_chat(chat_id)
    if not ch or ch["mp_status"] not in ("approved", "pending", "suspended", "draft"):
        return None, []
    await db.execute("UPDATE partner_channels SET mp_status = CASE WHEN mp_status IN ('approved','suspended') THEN 'suspended' "
                     "ELSE mp_status END, enabled = FALSE, mp_note = 'أُزيل البوت من القناة', updated_at = now() WHERE id = $1", ch["id"])
    refunded = []
    rows = await db.fetch("SELECT id, status FROM orders WHERE kind = 'tg_post' AND channel_chat_id = $1 "
                          "AND status IN ('submitted','in_progress','active')", chat_id)
    for r in rows:
        try:
            o = await _refund(int(r["id"]), "أُزيل البوت من القناة فتعذّر ضمان النشر — أُعيد المبلغ كاملاً",
                              ("submitted", "in_progress", "active"), counter="early_n" if r["status"] == "active" else "reject_n")
        except MPError:   # قيد النشر الآن — publish_one سيجد القناة موقوفة/يفشل ثم يُسترد لاحقاً
            o = None
        if o:
            refunded.append(o)
    await events.log_event("mp_bot_removed", ch["owner_user_id"], None, channel_id=ch["id"], refunded=len(refunded))
    return await PC.get(ch["id"]), refunded


# ═════════════════════════ الطلب: من الدفع إلى النشر ═════════════════════════

async def _bump(channel_id, counter: str) -> None:
    if counter in ("reject_n", "timeout_n", "early_n", "done_n") and channel_id:
        await db.execute(f"UPDATE partner_channels SET {counter} = {counter} + 1 WHERE id = $1", int(channel_id))


async def _refund(order_id: int, reason: str, expect: tuple, counter: str | None = None, new_status: str = "rejected",
                  admin_id: int | None = None) -> dict | None:
    from app.services import orders as orders_svc
    o = await repo.get(order_id)
    if not o:
        return None
    if o.get("publishing_until") and o["publishing_until"] > now() and o["status"] == "in_progress":
        raise MPError("البوت ينشر المنشور الآن — أعد المحاولة بعد دقيقة")
    upd = await orders_svc.refund(order_id, reason=reason, new_status=new_status, admin_id=admin_id, expect=expect)
    if upd and counter:
        await _bump((o.get("spec") or {}).get("channel_id"), counter)
    return upd


async def dispatch(order_id: int) -> tuple[dict | None, bool]:
    """بعد دفع العميل (أو بعد أن يكتب فريقنا النص): يربط الطلب بصاحب القناة ويبدأ مهلة القبول.

    يعيد (الطلب، هل يجب إرسال طلب القبول لصاحب القناة الآن؟)."""
    o = await repo.get(order_id)
    if not o or o.get("kind") != "tg_post" or o["status"] != "submitted":
        return None, False
    ch = await PC.get(int((o["spec"] or {}).get("channel_id") or 0))
    if not is_mp_channel(ch):
        return o, False
    ready = bool((o["spec"] or {}).get("text"))
    fields: dict = {"owner_user_id": ch["owner_user_id"], "channel_chat_id": ch["chat_id"]}
    send = ready and not o.get("owner_deadline")
    if send:
        fields["owner_deadline"] = now() + timedelta(hours=int((await cfg())["accept_hours"]))
    upd = await repo.transition(order_id, ("submitted",), **fields)
    if not upd:
        return None, False
    return await repo.get(order_id), send


async def owner_order(order_id: int, owner_id: int) -> dict:
    o = await repo.get(order_id)
    if not o or o.get("owner_user_id") != owner_id or o.get("kind") != "tg_post":
        raise MPError("الطلب غير موجود")
    return o


async def owner_accept(order_id: int, owner_id: int, when: datetime) -> dict:
    o = await owner_order(order_id, owner_id)
    if o["status"] != "submitted":
        raise MPError("الطلب لم يعد بانتظار ردك (أُلغي أو انتهت مهلته)")
    if not (o.get("spec") or {}).get("text"):
        raise MPError("نص المنشور لم يجهز بعد")
    upd = await repo.transition(order_id, ("submitted",), status="in_progress", scheduled_at=when, reminded_at=now(),
                                last_sync_at=now())
    if not upd:
        raise MPError("الطلب لم يعد بانتظار ردك (أُلغي أو انتهت مهلته)")
    await events.log_event("order_status", o["user_id"], order_id, from_="submitted", to="in_progress", owner_id=owner_id,
                           scheduled_at=when.isoformat())
    return await repo.get(order_id)


async def owner_reschedule_now(order_id: int, owner_id: int) -> dict:
    o = await owner_order(order_id, owner_id)
    upd = await repo.transition(order_id, ("in_progress",), scheduled_at=now())
    if not upd:
        raise MPError("الطلب لم يعد مجدولاً")
    return await repo.get(o["id"])


async def owner_reject(order_id: int, owner_id: int, reason: str) -> dict:
    o = await owner_order(order_id, owner_id)
    if o["status"] not in ("submitted", "in_progress"):
        raise MPError("لا يمكن الاعتذار الآن — الطلب نُشر أو انتهى")
    upd = await _refund(order_id, f"اعتذرت القناة: {reason}"[:250], ("submitted", "in_progress"), counter="reject_n")
    if not upd:
        raise MPError("الطلب تغيّرت حالته للتو")
    return upd


async def expire_requests(limit: int = 20) -> list[dict]:
    rows = await db.fetch("SELECT id FROM orders WHERE kind = 'tg_post' AND status = 'submitted' AND owner_user_id IS NOT NULL "
                          "AND owner_deadline IS NOT NULL AND owner_deadline < now() ORDER BY owner_deadline LIMIT $1", limit)
    out = []
    for r in rows:
        o = await _refund(int(r["id"]), "لم تردّ القناة خلال المهلة — أُعيد المبلغ كاملاً", ("submitted",), counter="timeout_n")
        if o:
            out.append(o)
    return out


# ——— النشر في القناة ———

async def _send_post(bot, chat_id: int, text: str | None, media: list[dict]) -> list[int]:
    from aiogram.types import InputMediaPhoto, InputMediaVideo
    from app.bot import texts as T
    cap = T.esc(text) if text else None
    visual = [m for m in media if m["kind"] in ("photo", "video")]
    docs = [m for m in media if m["kind"] == "document"]
    ids: list[int] = []
    if len(visual) == 1:
        m = visual[0]
        fn = bot.send_photo if m["kind"] == "photo" else bot.send_video
        msg = await fn(chat_id, m["file_id"], caption=cap)
        ids.append(msg.message_id)
    elif len(visual) > 1:
        group = []
        for i, m in enumerate(visual[:10]):
            cls = InputMediaPhoto if m["kind"] == "photo" else InputMediaVideo
            group.append(cls(media=m["file_id"], caption=cap if i == 0 else None))
        msgs = await bot.send_media_group(chat_id, group)
        ids += [x.message_id for x in msgs]
    for i, m in enumerate(docs):
        msg = await bot.send_document(chat_id, m["file_id"], caption=cap if (not ids and i == 0) else None)
        ids.append(msg.message_id)
    if not ids:
        msg = await bot.send_message(chat_id, cap or "📢")
        ids.append(msg.message_id)
    return ids


async def _delete_post(bot, chat_id: int, ids: list[int], pinned: bool) -> bool:
    ok = True
    if pinned and ids:
        try:
            await bot.unpin_chat_message(chat_id=chat_id, message_id=ids[0])
        except Exception as e:  # noqa: BLE001
            log.info("unpin failed %s/%s: %s", chat_id, ids[0], e)
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception as e:  # noqa: BLE001 — أقدم من 48 ساعة أو حُذف يدوياً
            log.info("delete post msg failed %s/%s: %s", chat_id, mid, e)
            ok = False
    return ok


async def publish_one(bot, order_id: int) -> tuple[str, dict | None]:
    """ينشر طلباً حان موعده. يعيد ('published'|'retry'|'refunded'|'skip', الطلب)."""
    got = await db.fetchval(
        "UPDATE orders SET publishing_until = now() + interval '3 minutes', publish_attempts = publish_attempts + 1 "
        "WHERE id = $1 AND kind = 'tg_post' AND status = 'in_progress' AND owner_user_id IS NOT NULL "
        "AND channel_chat_id IS NOT NULL AND (publishing_until IS NULL OR publishing_until < now()) RETURNING id", order_id)
    if not got:
        return "skip", None
    o = await repo.get(order_id)
    spec = o["spec"] or {}
    ch = await PC.get(int(spec.get("channel_id") or 0))
    chat_id = int(o["channel_chat_id"])
    try:
        ids = await _send_post(bot, chat_id, spec.get("text"), await repo.media(order_id))
    except Exception as e:  # noqa: BLE001
        log.warning("publish ORD-%s to %s failed: %s", order_id, chat_id, e)
        grace = int((await cfg())["publish_grace_hours"])
        if o.get("scheduled_at") and now() > o["scheduled_at"] + timedelta(hours=grace):
            await db.execute("UPDATE orders SET publishing_until = NULL WHERE id = $1", order_id)
            upd = await _refund(order_id, "تعذّر على البوت النشر في القناة — أُعيد المبلغ كاملاً", ("in_progress",), counter="reject_n")
            return ("refunded", upd) if upd else ("skip", None)
        await db.execute("UPDATE orders SET publishing_until = now() + interval '5 minutes', note = $2 WHERE id = $1",
                         order_id, f"تعذّر النشر (محاولة {o['publish_attempts']}): {str(e)[:120]}")
        return "retry", await repo.get(order_id)
    pinned = False
    if spec.get("format") == "pin":
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=ids[0], disable_notification=False)
            pinned = True
        except Exception as e:  # noqa: BLE001
            log.warning("pin ORD-%s failed: %s", order_id, e)
    uname = (ch or {}).get("username") or str(spec.get("channel_url", "")).rstrip("/").rsplit("/", 1)[-1]
    t = now()
    hours = int(spec.get("hours") or 24)
    upd = await repo.transition(order_id, ("in_progress",), status="active", post_url=f"https://t.me/{uname}/{ids[0]}",
                                started_at=t, ends_at=t + timedelta(hours=hours), channel_msg_ids=ids, publishing_until=None,
                                verified_at=t, last_sync_at=t, note=None,
                                results={**(o.get("results") or {}), "pinned": pinned})
    if not upd:
        # أُلغي/استُرد في اللحظة نفسها — لا نترك منشوراً غير مدفوع في القناة
        await _delete_post(bot, chat_id, ids, pinned)
        return "skip", None
    await events.log_event("order_status", o["user_id"], order_id, from_="in_progress", to="active", auto=True, msgs=len(ids))
    return "published", await repo.get(order_id)


async def publish_due(bot, limit: int = 10) -> list[tuple[str, dict]]:
    rows = await db.fetch(
        "SELECT id FROM orders WHERE kind = 'tg_post' AND status = 'in_progress' AND owner_user_id IS NOT NULL "
        "AND scheduled_at IS NOT NULL AND scheduled_at <= now() AND (publishing_until IS NULL OR publishing_until < now()) "
        "ORDER BY scheduled_at LIMIT $1", limit)
    out = []
    for r in rows:
        res, o = await publish_one(bot, int(r["id"]))
        if o and res != "skip":
            out.append((res, o))
    return out


# ——— التحقق من بقاء المنشور ———

async def post_exists(bot, chat_id: int, msg_id: int) -> bool | None:
    """True موجود · False محذوف · None غير معروف (شبكة/صلاحيات).

    الحيلة: تعديل أزرار الرسالة إلى «لا شيء» — منشورنا بلا أزرار، فتيليغرام يرد «message is not modified»
    إن كانت موجودة، و«message to edit not found» إن حُذفت. لا يغيّر شيئاً في القناة ولا يحتاج قناة سجلّ."""
    from aiogram.exceptions import TelegramBadRequest
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
        return True
    except TelegramBadRequest as e:
        s = str(e).lower()
        if "not modified" in s:
            return True
        if "not found" in s or "message_id_invalid" in s or "message to edit" in s:
            return False
        return None
    except Exception:  # noqa: BLE001
        return None


async def verify_due(bot, limit: int = 20) -> list[dict]:
    """منشورات فعّالة لم يُتحقق منها منذ verify_hours. المحذوف قبل آخر ساعة من المدة = استرداد كامل."""
    hours = int((await cfg())["verify_hours"])
    rows = await db.fetch(
        "SELECT id FROM orders WHERE kind = 'tg_post' AND status = 'active' AND owner_user_id IS NOT NULL "
        "AND channel_msg_ids IS NOT NULL AND jsonb_array_length(channel_msg_ids) > 0 "
        "AND ends_at > now() + interval '1 hour' AND (verified_at IS NULL OR verified_at < now() - ($1 || ' hours')::interval) "
        "ORDER BY verified_at NULLS FIRST LIMIT $2", str(hours), limit)
    out = []
    for r in rows:
        o = await repo.get(int(r["id"]))
        results = [await post_exists(bot, int(o["channel_chat_id"]), int(m)) for m in (o.get("channel_msg_ids") or [])]
        if any(x is False for x in results):
            upd = await _refund(o["id"], "حُذف المنشور من القناة قبل انتهاء مدته — أُعيد المبلغ كاملاً", ("active",), counter="early_n")
            if upd:
                await events.log_event("mp_early_delete", o["owner_user_id"], o["id"])
                out.append(upd)
            continue
        await db.execute("UPDATE orders SET verified_at = now() WHERE id = $1", o["id"])
    return out


async def finish_due(bot, limit: int = 20) -> list[dict]:
    """انتهت المدة: الطلب ✅ (ربح صاحب القناة يُحجز عبر PP.finish ← on_completed) ثم يحذف البوت المنشور."""
    from app.services import partner_posts as PP
    rows = await db.fetch("SELECT id FROM orders WHERE kind = 'tg_post' AND status = 'active' AND owner_user_id IS NOT NULL "
                          "AND ends_at IS NOT NULL AND ends_at <= now() ORDER BY ends_at LIMIT $1", limit)
    out = []
    for r in rows:
        upd = await PP.finish(int(r["id"]), None)
        if not upd:
            continue
        ids = [int(x) for x in (upd.get("channel_msg_ids") or [])]
        if ids and upd.get("channel_chat_id"):
            ok = await _delete_post(bot, int(upd["channel_chat_id"]), ids, bool((upd.get("results") or {}).get("pinned")))
            await db.execute("UPDATE orders SET unpublished_at = CASE WHEN $2 THEN now() END WHERE id = $1", upd["id"], ok)
        out.append(await repo.get(upd["id"]))
    return out


# ═════════════════════════ الأرباح ═════════════════════════

async def _move(c, user_id: int, held: Decimal = Decimal("0"), avail: Decimal = Decimal("0"), *, type_: str,
                ref_type: str | None = None, ref_id: int | None = None, note: str | None = None, admin_id: int | None = None) -> None:
    """داخل معاملة قائمة وبعد lock_user: سطر(ا) دفتر + تحديث العمودين معاً. القيد CHECK يمنع السالب."""
    for bucket, amt in (("held", held), ("avail", avail)):
        if amt:
            await c.execute("INSERT INTO earnings_ledger (user_id, bucket, type, amount_usd, ref_type, ref_id, note, admin_id) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", user_id, bucket, type_, money(amt), ref_type, ref_id, note, admin_id)
    await c.execute("UPDATE users SET earn_held_usd = earn_held_usd + $2, earn_avail_usd = earn_avail_usd + $3 WHERE tg_id = $1",
                    user_id, money(held), money(avail))


async def on_completed(order: dict) -> bool:
    """يحجز ربح صاحب القناة (= cost_usd) مرة واحدة فقط لكل طلب."""
    owner = order.get("owner_user_id")
    cost = money(Decimal(str(order.get("cost_usd") or 0)))
    if not owner or cost <= 0:
        return False
    hold = int((await cfg())["hold_hours"])
    oid = int(order["id"])
    async with db.pool().acquire() as c:
        async with c.transaction():
            await money_svc.lock_user(c, owner)
            try:
                await money_svc.claim_key(c, f"mp:earn:{oid}", owner)
            except money_svc.DuplicateOperation:
                return False
            await _move(c, owner, held=cost, type_="earning", ref_type="order", ref_id=oid,
                        note=f"ORD-{oid} منشور في {(order.get('spec') or {}).get('channel_title', '')}"[:200])
            await c.execute("UPDATE orders SET payout_status = 'held', payout_at = now() + ($2 || ' hours')::interval WHERE id = $1",
                            oid, str(hold))
    await _bump((order.get("spec") or {}).get("channel_id"), "done_n")
    await events.log_event("mp_earning_held", owner, oid, amount=str(cost))
    return True


async def release_one(order_id: int) -> dict | None:
    row = await db.fetchrow("SELECT owner_user_id, cost_usd FROM orders WHERE id = $1", order_id)
    if not row or not row["owner_user_id"]:
        return None
    owner, cost = int(row["owner_user_id"]), money(Decimal(row["cost_usd"]))
    async with db.pool().acquire() as c:
        async with c.transaction():
            await money_svc.lock_user(c, owner)
            ok = await c.fetchval("UPDATE orders SET payout_status = 'available' WHERE id = $1 AND payout_status = 'held' "
                                  "AND payout_at <= now() RETURNING id", order_id)
            if not ok:
                return None
            await _move(c, owner, held=-cost, avail=cost, type_="release", ref_type="order", ref_id=order_id,
                        note=f"ORD-{order_id} انتهت فترة البلاغات")
    await events.log_event("mp_earning_released", owner, order_id, amount=str(cost))
    return await repo.get(order_id)


async def release_due(limit: int = 50) -> list[dict]:
    rows = await db.fetch("SELECT id FROM orders WHERE payout_status = 'held' AND payout_at <= now() ORDER BY payout_at LIMIT $1", limit)
    out = []
    for r in rows:
        o = await release_one(int(r["id"]))
        if o:
            out.append(o)
    return out


async def earnings(user_id: int) -> dict:
    row = await db.fetchrow("SELECT earn_held_usd, earn_avail_usd FROM users WHERE tg_id = $1", user_id)
    paid = await db.fetchval("SELECT COALESCE(sum(amount_usd), 0) FROM payouts WHERE user_id = $1 AND status = 'paid'", user_id)
    conv = await db.fetchval("SELECT COALESCE(-sum(amount_usd), 0) FROM earnings_ledger WHERE user_id = $1 AND type = 'convert'", user_id)
    pend = await db.fetchrow("SELECT * FROM payouts WHERE user_id = $1 AND status = 'pending' LIMIT 1", user_id)
    return {"held": money(Decimal(row["earn_held_usd"] if row else 0)), "avail": money(Decimal(row["earn_avail_usd"] if row else 0)),
            "paid": money(Decimal(paid)), "converted": money(Decimal(conv)), "pending": dict(pend) if pend else None}


async def earnings_history(user_id: int, limit: int = 10) -> list[dict]:
    rows = await db.fetch("SELECT * FROM earnings_ledger WHERE user_id = $1 ORDER BY id DESC LIMIT $2", user_id, limit)
    return [dict(r) for r in rows]


async def earnings_mismatch() -> list[dict]:
    """فحص سلامة: عمودا المستخدم = مجموع دفتر الأرباح لكل bucket."""
    rows = await db.fetch(
        "SELECT u.tg_id, u.earn_held_usd, u.earn_avail_usd, "
        "COALESCE(sum(e.amount_usd) FILTER (WHERE e.bucket = 'held'), 0) AS lh, "
        "COALESCE(sum(e.amount_usd) FILTER (WHERE e.bucket = 'avail'), 0) AS la "
        "FROM users u LEFT JOIN earnings_ledger e ON e.user_id = u.tg_id GROUP BY u.tg_id "
        "HAVING u.earn_held_usd <> COALESCE(sum(e.amount_usd) FILTER (WHERE e.bucket = 'held'), 0) "
        "    OR u.earn_avail_usd <> COALESCE(sum(e.amount_usd) FILTER (WHERE e.bucket = 'avail'), 0)")
    return [dict(r) for r in rows]


# ——— بلاغ العميل خلال فترة الحجز ———

async def open_dispute(order_id: int, user_id: int) -> dict:
    ok = await db.fetchval("UPDATE orders SET payout_status = 'disputed', updated_at = now() WHERE id = $1 AND user_id = $2 "
                           "AND status = 'completed' AND payout_status = 'held' AND payout_at > now() RETURNING id", order_id, user_id)
    if not ok:
        raise MPError("انتهت فترة البلاغات لهذا الطلب أو سبق الإبلاغ عنه")
    await events.log_event("mp_dispute_open", user_id, order_id)
    return await repo.get(order_id)


async def resolve_dispute(order_id: int, admin_id: int, refund_customer: bool) -> dict | None:
    from app.services import orders as orders_svc
    if not refund_customer:
        ok = await db.fetchval("UPDATE orders SET payout_status = 'held', updated_at = now() WHERE id = $1 "
                               "AND payout_status = 'disputed' RETURNING id", order_id)
        if ok:
            await events.log_event("mp_dispute_kept", None, order_id, admin_id=admin_id)
        return await repo.get(order_id) if ok else None
    row = await db.fetchrow("SELECT owner_user_id, cost_usd FROM orders WHERE id = $1", order_id)
    if not row or not row["owner_user_id"]:
        return None
    owner, cost = int(row["owner_user_id"]), money(Decimal(row["cost_usd"]))
    async with db.pool().acquire() as c:
        async with c.transaction():
            await money_svc.lock_user(c, owner)
            ok = await c.fetchval("UPDATE orders SET payout_status = 'reversed' WHERE id = $1 AND payout_status = 'disputed' "
                                  "RETURNING id", order_id)
            if not ok:
                return None
            await _move(c, owner, held=-cost, type_="reverse", ref_type="order", ref_id=order_id,
                        note=f"ORD-{order_id} بلاغ مقبول — استرداد للعميل", admin_id=admin_id)
    upd = await orders_svc.refund(order_id, reason="بلاغ مقبول: المنشور لم يُنفَّذ كما يجب — أُعيد المبلغ كاملاً",
                                  new_status="refunded", admin_id=admin_id, expect=("completed",))
    await events.log_event("mp_dispute_refunded", None, order_id, admin_id=admin_id)
    return upd or await repo.get(order_id)


# ——— السحب والتحويل ———

_TRC20 = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_BEP20 = re.compile(r"^0x[0-9a-fA-F]{40}$")


def clean_address(method: str, raw: str) -> str:
    s = (raw or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if method == "usdt_trc20":
        if not _TRC20.match(s):
            raise MPError("عنوان TRC20 يبدأ بحرف T وطوله 34 خانة — انسخه كما هو من محفظتك")
    elif method == "usdt_bep20":
        if not _BEP20.match(s):
            raise MPError("عنوان BEP20 يبدأ بـ 0x ويتبعه 40 خانة — انسخه كما هو من محفظتك")
    elif method == "shamcash":
        s = " ".join(s.split())
        if not 6 <= len(s) <= 60:
            raise MPError("اكتب رقم/معرّف حساب شام كاش (6 خانات على الأقل)")
    else:
        raise MPError("طريقة سحب غير معروفة")
    return s


async def request_payout(user_id: int, amount: Decimal, method: str, address: str) -> dict:
    import asyncpg
    amount = money(Decimal(str(amount)))
    mn = money(Decimal(str((await cfg())["min_payout"])))
    if amount < mn:
        raise MPError(f"الحد الأدنى للسحب {mn}$")
    if method not in PAYOUT_METHODS:
        raise MPError("طريقة سحب غير معروفة")
    address = clean_address(method, address)
    try:
        async with db.pool().acquire() as c:
            async with c.transaction():
                await money_svc.lock_user(c, user_id)
                avail = Decimal(await c.fetchval("SELECT earn_avail_usd FROM users WHERE tg_id = $1", user_id) or 0)
                if avail < amount:
                    raise MPError(f"أرباحك المتاحة {money(avail)}$ فقط")
                pid = await c.fetchval("INSERT INTO payouts (user_id, amount_usd, method, address) VALUES ($1,$2,$3,$4) RETURNING id",
                                       user_id, amount, method, address)
                await _move(c, user_id, avail=-amount, type_="payout", ref_type="payout", ref_id=pid,
                            note=f"PAY-{pid} {PAYOUT_METHODS[method]}")
    except asyncpg.UniqueViolationError:
        raise MPError("عندك طلب سحب قيد المراجعة — انتظر حتى يُنفَّذ") from None
    await events.log_event("mp_payout_request", user_id, None, payout_id=pid, amount=str(amount), method=method)
    return await get_payout(pid)


async def get_payout(pid: int) -> dict | None:
    row = await db.fetchrow("SELECT p.*, u.name AS user_name, u.username AS user_username FROM payouts p "
                            "JOIN users u ON u.tg_id = p.user_id WHERE p.id = $1", pid)
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("admin_msg_ids"), str):
        import json
        d["admin_msg_ids"] = json.loads(d["admin_msg_ids"] or "[]")
    return d


async def decide_payout(pid: int, admin_id: int, paid: bool, note: str = "") -> dict | None:
    p = await get_payout(pid)
    if not p:
        return None
    async with db.pool().acquire() as c:
        async with c.transaction():
            await money_svc.lock_user(c, p["user_id"])
            ok = await c.fetchval("UPDATE payouts SET status = $2, admin_id = $3, note = $4, decided_at = now() "
                                  "WHERE id = $1 AND status = 'pending' RETURNING id", pid, "paid" if paid else "rejected",
                                  admin_id, (note or "")[:200])
            if not ok:
                return None
            if not paid:
                await _move(c, p["user_id"], avail=money(Decimal(p["amount_usd"])), type_="payout_return", ref_type="payout",
                            ref_id=pid, note=f"PAY-{pid} رُفض: {note}"[:200], admin_id=admin_id)
    await events.log_event("mp_payout_" + ("paid" if paid else "rejected"), p["user_id"], None, payout_id=pid, admin_id=admin_id)
    return await get_payout(pid)


async def set_payout_msgs(pid: int, pairs: list[list[int]]) -> None:
    import json
    await db.execute("UPDATE payouts SET admin_msg_ids = $2::jsonb WHERE id = $1", pid, json.dumps(pairs))


async def convert_to_balance(user_id: int, amount: Decimal) -> Decimal:
    """🔄 أرباح متاحة ← رصيد إعلانات فوراً (بلا حد أدنى). يعيد الرصيد الجديد."""
    amount = money(Decimal(str(amount)))
    if amount <= 0:
        raise MPError("المبلغ يجب أن يكون أكبر من صفر")
    async with db.pool().acquire() as c:
        async with c.transaction():
            await money_svc.lock_user(c, user_id)
            avail = Decimal(await c.fetchval("SELECT earn_avail_usd FROM users WHERE tg_id = $1", user_id) or 0)
            if avail < amount:
                raise MPError(f"أرباحك المتاحة {money(avail)}$ فقط")
            await _move(c, user_id, avail=-amount, type_="convert", note="تحويل إلى رصيد الإعلانات")
            bal = await money_svc.credit(user_id, amount, "earnings", ref_type="earnings", note="تحويل من أرباح القناة", conn=c)
    await events.log_event("mp_convert", user_id, None, amount=str(amount))
    return bal


# ——— التقييم ———

async def rate(order_id: int, user_id: int, stars: int) -> dict:
    if not 1 <= stars <= 5:
        raise MPError("التقييم من 1 إلى 5")
    cid = await db.fetchval("UPDATE orders SET rating = $3, updated_at = now() WHERE id = $1 AND user_id = $2 AND kind = 'tg_post' "
                            "AND status = 'completed' AND rating IS NULL RETURNING (spec->>'channel_id')::bigint", order_id, user_id, stars)
    if cid is None:
        raise MPError("قيّمت هذا الطلب مسبقاً 🌟")
    await db.execute("UPDATE partner_channels SET rating_sum = rating_sum + $2, rating_n = rating_n + 1 WHERE id = $1", cid, stars)
    await events.log_event("tgp_rated", user_id, order_id, stars=stars)
    return await PC.get(int(cid))


def rating_label(ch: dict) -> str:
    n = int(ch.get("rating_n") or 0)
    if not n:
        return ""
    return f"⭐ {ch['rating_sum'] / n:.1f} ({n})"


# ═════════════════════════ استعلامات اللوحات ═════════════════════════

async def owner_orders(owner_id: int, open_only: bool = True, limit: int = 15) -> list[dict]:
    cond = "AND o.status IN ('submitted','in_progress','active')" if open_only else ""
    rows = await db.fetch(f"SELECT o.* FROM orders o WHERE o.owner_user_id = $1 AND o.kind = 'tg_post' {cond} "
                          f"AND (o.status <> 'submitted' OR o.owner_deadline IS NOT NULL) ORDER BY o.id DESC LIMIT $2", owner_id, limit)
    return [repo.row_to_dict(r) for r in rows]


async def owner_counts(owner_id: int) -> dict:
    row = await db.fetchrow(
        "SELECT count(*) FILTER (WHERE status = 'submitted' AND owner_deadline IS NOT NULL) AS waiting, "
        "count(*) FILTER (WHERE status IN ('in_progress','active')) AS running, "
        "count(*) FILTER (WHERE status = 'completed') AS done FROM orders WHERE owner_user_id = $1 AND kind = 'tg_post'", owner_id)
    return dict(row)


async def admin_counts() -> dict:
    row = await db.fetchrow(
        "SELECT (SELECT count(*) FROM partner_channels WHERE mp_status = 'pending' AND NOT archived) AS pending, "
        "(SELECT count(*) FROM partner_channels WHERE mp_status = 'approved' AND enabled AND NOT archived) AS live, "
        "(SELECT count(*) FROM partner_channels WHERE owner_user_id IS NOT NULL AND NOT archived AND mp_status <> 'draft') AS total, "
        "(SELECT count(*) FROM payouts WHERE status = 'pending') AS payouts, "
        "(SELECT COALESCE(sum(amount_usd), 0) FROM payouts WHERE status = 'pending') AS payouts_sum, "
        "(SELECT count(*) FROM orders WHERE payout_status = 'disputed') AS disputes, "
        "(SELECT COALESCE(sum(earn_held_usd), 0) FROM users) AS held, "
        "(SELECT COALESCE(sum(earn_avail_usd), 0) FROM users) AS avail, "
        "(SELECT COALESCE(sum(price_usd - cost_usd), 0) FROM orders WHERE owner_user_id IS NOT NULL AND status = 'completed' "
        "   AND payout_status <> 'reversed' AND completed_at >= date_trunc('month', now())) AS commission_month, "
        "(SELECT count(*) FROM orders WHERE owner_user_id IS NOT NULL AND status = 'completed' "
        "   AND completed_at >= date_trunc('month', now())) AS posts_month")
    return dict(row)


async def admin_channels(status: str | None = None, limit: int = 30) -> list[dict]:
    rows = await db.fetch("SELECT * FROM partner_channels WHERE owner_user_id IS NOT NULL AND NOT archived AND mp_status <> 'draft' "
                          "AND ($1::text IS NULL OR mp_status = $1) ORDER BY (mp_status = 'pending') DESC, id DESC LIMIT $2",
                          status, limit)
    return [PC._d(r) for r in rows]


async def pending_payouts(limit: int = 20) -> list[dict]:
    rows = await db.fetch("SELECT p.*, u.name AS user_name, u.username AS user_username FROM payouts p JOIN users u ON u.tg_id = p.user_id "
                          "WHERE p.status = 'pending' ORDER BY p.id LIMIT $1", limit)
    return [dict(r) for r in rows]


async def disputes(limit: int = 20) -> list[dict]:
    rows = await db.fetch("SELECT * FROM orders WHERE payout_status = 'disputed' ORDER BY id LIMIT $1", limit)
    return [repo.row_to_dict(r) for r in rows]


async def refresh_subs(bot, limit: int = 5) -> int:
    rows = await db.fetch("SELECT id, chat_id FROM partner_channels WHERE mp_status = 'approved' AND NOT archived AND chat_id IS NOT NULL "
                          "AND (subs_checked_at IS NULL OR subs_checked_at < now() - interval '24 hours') ORDER BY subs_checked_at NULLS FIRST LIMIT $1", limit)
    n = 0
    for r in rows:
        try:
            cnt = int(await bot.get_chat_member_count(int(r["chat_id"])))
        except Exception as e:  # noqa: BLE001
            log.info("subs refresh failed for channel %s: %s", r["id"], e)
            await db.execute("UPDATE partner_channels SET subs_checked_at = now() WHERE id = $1", r["id"])
            continue
        await db.execute("UPDATE partner_channels SET subscribers = $2, subs_checked_at = now() WHERE id = $1", r["id"], cnt)
        n += 1
    return n
