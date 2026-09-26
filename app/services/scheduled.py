"""📅 «باقة تصميم يومي» — المنطق كله هنا.

النموذج المعتمد:
    • العقد = عدد تسليمات (N) لا أيام — تنتهي بعد N تصميم فعلي.
    • كل زوج (🖼️ تصميم + ✍️ نصّه) يدخل طابور الاشتراك FIFO (seq تلقائي).
    • الإرسال: زوج واحد كل يوم في ساعة الاشتراك — caption (الصورة ونصها تحتها برسالة واحدة).
    • وجه التسليم: محادثة الزبون أو قناة خاصة له (البوت مشرفاً فيها) — عزل تام: الزوج يُرسل لوجه بطاقته فقط.
    • الطابور فارغ عند الموعد؟ انتظار صامت — أول زوج يصل يُرسل فوراً مع اعتذار (missed_slot).
    • الإلغاء: استرداد نسبي بالمتباقي `السعر × المتبقي/N` — للاشتراكات المدفوعة بالبوت فقط.

الإيقاع بعد كل إرسال:
    • عادي: الموعد اليومي التالي (أول ظهور للساعة بعد الآن + 18 ساعة) — لا يزيد على إرسال واحد في اليوم.
    • سلسلة لحاق (missed): التالي بعد ساعة (CATCHUP_GAP_MINUTES) حتى يُلحق الطابور ثم يستأنف الإيقاع اليومي.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError

from app.db import pool as db
from app.db.repo import scheduled as repo
from app.db.repo import settings as settings_repo
from app.services import money
from app.services.pricing import money as money_amount

log = logging.getLogger("scheduled")

PACKAGES_KEY = "scheduled_design_packages"
DEFAULT_TIMEZONE = "Asia/Damascus"
CATCHUP_GAP_MINUTES = 60      # الفجوة بين تسليمات اللحاق المتأخر
SLOT_MIN_GAP_HOURS = 18       # لا يقل الفارق بين إرسالين عاديين (إيقاع يومي واحد)
COPY_MAX = 1024               # حد caption في تيليغرام
LOW_BUFFER_DAYS = 3           # 🚨 تنبيه الاحتياطي


def _defaults() -> list[dict]:
    return []


def _time_value(value: Any):
    if hasattr(value, "hour"):
        return value.replace(second=0, microsecond=0)
    raw = str(value or "20:00").strip()
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return dtime(hour, minute)
    except (ValueError, TypeError):
        raise ValueError("وقت الإرسال يجب أن يكون بصيغة HH:MM") from None


# ───────────────── الباقات ─────────────────

async def packages(include_disabled: bool = True) -> list[dict]:
    value = await settings_repo.get(PACKAGES_KEY, _defaults())
    rows = value if isinstance(value, list) else []
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item["enabled"] = bool(item.get("enabled", True))
        if include_disabled or item["enabled"]:
            out.append(item)
    return out


async def get_package(code: str, enabled_only: bool = False) -> dict | None:
    code = str(code or "").strip()
    for p in await packages(include_disabled=not enabled_only):
        if p.get("code") == code and (not enabled_only or p.get("enabled")):
            return p
    return None


def validate_package(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("بيانات الباقة غير صالحة")
    code = str(raw.get("code") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", code):
        raise ValueError("رمز الباقة: أحرف إنجليزية وأرقام و _ أو - (من 2 إلى 40)")
    title = str(raw.get("title") or "").strip()[:100]
    if len(title) < 2:
        raise ValueError("اسم الباقة مطلوب")
    desc = str(raw.get("description") or "").strip()[:1000]
    try:
        price = money_amount(Decimal(str(raw.get("price_usd"))))
        total = int(raw.get("total_items"))
        days = int(raw.get("duration_days") or total)   # المدة شكلية — العقد بالعدد
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("السعر وعدد التصاميم والأيام يجب أن تكون أرقاماً صحيحة") from None
    if price < 1 or price > 100000:
        raise ValueError("السعر يجب أن يكون بين 1 و 100000")
    if not 1 <= total <= 365:
        raise ValueError("عدد التسليمات بين 1 و365")
    if not 1 <= days <= 365:
        raise ValueError("المدة بين 1 و365 يوماً")
    send_time = str(raw.get("send_time") or "20:00").strip()
    parsed_time = _time_value(send_time)
    return {
        "code": code,
        "title": title,
        "description": desc,
        "price_usd": f"{price:.2f}",
        "total_items": total,
        "duration_days": days,
        "send_time": parsed_time.strftime("%H:%M"),
        "timezone": str(raw.get("timezone") or DEFAULT_TIMEZONE)[:64],
        "include_copy": bool(raw.get("include_copy", True)),
        "enabled": bool(raw.get("enabled", True)),
    }


async def save_package(raw: dict, admin_id: int | None = None) -> tuple[dict, bool]:
    clean = validate_package(raw)
    rows = await packages()
    existed = any(p.get("code") == clean["code"] for p in rows)
    rows = [clean if p.get("code") == clean["code"] else p for p in rows]
    if not existed:
        rows.append(clean)
    await settings_repo.set_(PACKAGES_KEY, rows)
    return clean, existed


async def toggle_package(code: str, enabled: bool) -> dict | None:
    rows = await packages()
    found = None
    for p in rows:
        if p.get("code") == code:
            p["enabled"] = bool(enabled)
            found = p
            break
    if found is None:
        return None
    await settings_repo.set_(PACKAGES_KEY, rows)
    return found


async def delete_package(code: str) -> bool:
    rows = await packages()
    new = [p for p in rows if p.get("code") != code]
    if len(new) == len(rows):
        return False
    await settings_repo.set_(PACKAGES_KEY, new)
    return True


# ───────────────── المواعيد (الإيقاع اليومي) ─────────────────

def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:  # noqa: BLE001
        return ZoneInfo(DEFAULT_TIMEZONE)


def _slot_on(day, hh: int, mm: int, tz) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)


def first_slot(send_time: str, tz_name: str | None, now: datetime | None = None) -> datetime:
    """أول ظهور قادم للساعة (UTC)."""
    tz = _tz(tz_name)
    t = _time_value(send_time)
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(tz)
    cand = _slot_on(local.date(), t.hour, t.minute, tz)
    if cand <= now.astimezone(tz):
        cand += timedelta(days=1)
    return cand.astimezone(timezone.utc)


def next_daily_slot(send_time: str, tz_name: str | None, now: datetime | None = None) -> datetime:
    """الموعد اليومي التالي — أول ظهور للساعة يبعد ≥ SLOT_MIN_GAP_HOURS (لا إرسالان في اليوم)."""
    tz = _tz(tz_name)
    t = _time_value(send_time)
    now = now or datetime.now(timezone.utc)
    base = (now + timedelta(hours=SLOT_MIN_GAP_HOURS)).astimezone(tz)
    cand = _slot_on(base.date(), t.hour, t.minute, tz)
    if cand < base:
        cand += timedelta(days=1)
    return cand.astimezone(timezone.utc)


def catchup_slot(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) + timedelta(minutes=CATCHUP_GAP_MINUTES)


def buffer_state(ready: int) -> str:
    if ready < LOW_BUFFER_DAYS:
        return "low"
    if ready < 7:
        return "warn"
    return "ok"


# ───────────────── وجه التسليم ─────────────────

async def resolve_target(bot, raw: str) -> dict:
    """يتحقق من الوجه (قناة/محادثة) عبر تيليغرام ويعيد بياناته الحقيقية (لا نثق بما كتب الأدمن)."""
    s = str(raw or "").strip()
    if not s:
        raise ValueError("أدخل معرّف القناة أو محادثة الزبون")
    ref: Any = int(s) if re.fullmatch(r"-?\d{4,}", s) else s
    try:
        chat = await bot.get_chat(ref)
    except Exception:  # noqa: BLE001
        raise ValueError("لم أجد هذا الوجه — تأكد أن البوت مضاف (مشرفاً) في القناة، أو أرسل معرّفاً/رقماً صحيحاً") from None
    kind = "channel" if chat.type in ("channel", "supergroup") else "user"
    title = chat.title or getattr(chat, "full_name", None) or str(chat.id)
    if kind == "channel":
        try:
            me = await bot.get_chat_member(chat.id, bot.id)
        except Exception:  # noqa: BLE001
            raise ValueError("البوت ليس عضواً في هذه القناة — أضفه مشرفاً بصلاحية «نشر الرسائل» أولاً") from None
        can_post = getattr(me, "can_post", None)
        if chat.type == "channel" and me.status not in ("creator", "administrator") or (me.status == "administrator" and can_post is False):
            raise ValueError("البوت بلا صلاحية «نشر الرسائل» في هذه القناة — فعّلها من إعدادات القناة")
    return {"chat_id": int(chat.id), "kind": kind, "title": str(title)[:120]}


# ───────────────── الإنشاء ─────────────────

def _package_spec(package: dict) -> dict:
    return {
        "scheduled_subscription": True,
        "package_code": package.get("code") or "custom",
        "package_title": package.get("title") or "باقة تصميم",
        "total_items": int(package["total_items"]),
        "duration_days": int(package.get("duration_days") or package["total_items"]),
        "send_time": package.get("send_time") or "20:00",
        "timezone": package.get("timezone") or DEFAULT_TIMEZONE,
        "include_copy": bool(package.get("include_copy", True)),
    }


class DuplicatePurchase(Exception):
    def __init__(self, ref_id: int):
        super().__init__(ref_id)
        self.ref_id = int(ref_id)


async def purchase(user_id: int, code: str, checkout_key: str | None = None) -> dict:
    """شراء باقة من الرصيد — وجه التسليم محادثة الزبون، والإيقاع يبدأ فوراً (طابور يُملأ لاحقاً)."""
    package = await get_package(code, enabled_only=True)
    if not package:
        raise ValueError("الباقة غير متاحة حالياً")
    price = money_amount(Decimal(str(package["price_usd"])))
    spec = _package_spec(package)
    try:
        async with db.pool().acquire() as c:
            async with c.transaction():
                balance = await money.lock_user(c, user_id)
                if checkout_key:
                    existing = await c.fetchval(
                        "SELECT id FROM scheduled_subscriptions WHERE checkout_key=$1 AND user_id=$2",
                        checkout_key, user_id)
                    if existing:
                        raise DuplicatePurchase(existing)
                if balance < price:
                    raise money.InsufficientBalance(balance, price)
                order = await c.fetchrow(
                    """
                    INSERT INTO orders (user_id, kind, status, spec, price_usd, cost_usd, paid_at, note, checkout_key)
                    VALUES ($1, 'design', 'active', $2::jsonb, $3, 0, now(), $4, $5)
                    RETURNING id
                    """,
                    user_id, __import__("json").dumps(spec, ensure_ascii=False), price,
                    f"اشتراك تصميم يومي — {package['title']}", checkout_key)
                sub = await repo.create_sub(
                    user_id=user_id, order_id=order["id"],
                    package_code=package["code"], package_title=package["title"],
                    price_usd=price, total_items=int(package["total_items"]),
                    duration_days=int(package.get("duration_days") or package["total_items"]),
                    send_time=package.get("send_time") or "20:00",
                    timezone_name=package.get("timezone") or DEFAULT_TIMEZONE,
                    target_chat_id=user_id, target_kind="user", target_title=None,
                    customer_name=None, is_manual=False, note=None,
                    checkout_key=checkout_key,
                    next_send_at=first_slot(package.get("send_time") or "20:00",
                                            package.get("timezone") or DEFAULT_TIMEZONE),
                    conn=c)
                await c.execute("UPDATE orders SET scheduled_subscription_id=$2 WHERE id=$1", order["id"], sub["id"])
                await c.execute(
                    "INSERT INTO ledger (user_id,type,amount_usd,ref_type,ref_id,note) "
                    "VALUES ($1,'order_charge',$2,'subscription',$3,$4)",
                    user_id, -price, sub["id"], f"SUB-{sub['id']} {package['title']}")
                await c.execute("UPDATE users SET balance_usd=balance_usd-$2 WHERE tg_id=$1", user_id, price)
    except DuplicatePurchase as d:
        dup = await repo.get_subscription(d.ref_id)
        dup = dict(dup) if dup else {"id": d.ref_id, "package_title": package["title"]}
        dup["duplicate"] = True
        return dup
    result = await repo.get_subscription(int(sub["id"]))
    if not result:
        raise RuntimeError("تعذر إنشاء الاشتراك")
    return result


async def create_manual(admin_id: int, *, name: str, target_ref: str, total_items: int,
                        send_time: str = "20:00", package_title: str = "", note: str = "",
                        price_ref: str = "", bot=None) -> dict:
    """اشتراك يدوي لزبون قائم (دفع برّا البوت) — وجهه قناة أو محادثته."""
    if bot is None:
        raise ValueError("خدمة البوت غير متاحة")
    name = (name or "").strip()[:100]
    if len(name) < 2:
        raise ValueError("اسم الزبون مطلوب")
    if not 1 <= int(total_items) <= 365:
        raise ValueError("عدد التسليمات بين 1 و365")
    send_time = _time_value(send_time).strftime("%H:%M")
    target = await resolve_target(bot, target_ref)
    existing = await repo.active_on_target(target["chat_id"])
    if existing:
        raise ValueError(
            f"هذا الوجه مربوط باشتراك نشط SUB-{existing['id']} — لتمديد العدد افتحه وغيّر «عدد التسليمات»")
    price = Decimal("0")
    if str(price_ref or "").strip():
        try:
            price = money_amount(Decimal(str(price_ref).strip()))
        except (InvalidOperation, ValueError):
            raise ValueError("السعر المرجعي رقم بالدولار") from None
    sub = await repo.create_sub(
        user_id=None, order_id=None,
        package_code="manual", package_title=(package_title or "اشتراك يدوي").strip()[:100],
        price_usd=price, total_items=int(total_items), duration_days=int(total_items),
        send_time=send_time, timezone_name=DEFAULT_TIMEZONE,
        target_chat_id=target["chat_id"], target_kind=target["kind"], target_title=target["title"],
        customer_name=name, is_manual=True, note=(note or "")[:300] or None,
        checkout_key=None,
        next_send_at=first_slot(send_time, DEFAULT_TIMEZONE))
    return await repo.get_subscription(sub["id"])


async def extend(subscription_id: int, add_count: int, add_price, *, user_id: int | None = None,
                 checkout_key: str | None = None) -> dict:
    """تجديد/تمديد: تُضاف التسليمات للعقد نفسه (طابور واحد، عدّاد واحد)."""
    if not 1 <= int(add_count) <= 365:
        raise ValueError("عدد التسليمات المضافة بين 1 و365")
    sub = await repo.get_subscription(subscription_id)
    if not sub or sub["status"] not in ("scheduled", "paused", "completed"):
        raise ValueError("لا يمكن تمديد هذا الاشتراك")
    add_price = money_amount(Decimal(str(add_price or 0)))
    async with db.pool().acquire() as c:
        async with c.transaction():
            if add_price > 0 and user_id is not None:
                balance = await money.lock_user(c, user_id)
                if checkout_key:
                    existing = await c.fetchval(
                        "SELECT id FROM scheduled_subscriptions WHERE checkout_key=$1 AND user_id=$2",
                        checkout_key, user_id)
                    if existing:
                        raise DuplicatePurchase(existing)
                if balance < add_price:
                    raise money.InsufficientBalance(balance, add_price)
                spec = {"scheduled_subscription": True, "package_code": sub["package_code"],
                        "package_title": f"تمديد {sub['package_title']} (+{int(add_count)})",
                        "total_items": int(add_count), "duration_days": int(add_count),
                        "send_time": str(sub.get("send_time") or "20:00")[:5],
                        "timezone": (sub.get("timezone") or sub.get("timezone_name")) or DEFAULT_TIMEZONE,
                        "include_copy": True}
                await c.fetchrow(
                    "INSERT INTO orders (user_id, kind, status, spec, price_usd, cost_usd, paid_at, note, checkout_key, scheduled_subscription_id) "
                    "VALUES ($1,'design','active',$2::jsonb,$3,0,now(),$4,$5,$6) RETURNING id",
                    user_id, __import__("json").dumps(spec, ensure_ascii=False), add_price,
                    f"تمديد SUB-{subscription_id}", checkout_key, subscription_id)
                await c.execute(
                    "INSERT INTO ledger (user_id,type,amount_usd,ref_type,ref_id,note) VALUES ($1,'order_charge',$2,'subscription',$3,$4)",
                    user_id, -add_price, subscription_id, f"تمديد SUB-{subscription_id} (+{int(add_count)})")
                await c.execute("UPDATE users SET balance_usd=balance_usd-$2 WHERE tg_id=$1", user_id, add_price)
            upd = await c.fetchrow(
                "UPDATE scheduled_subscriptions SET total_items = total_items + $2, price_usd = price_usd + $3, "
                "status = CASE WHEN status='completed' THEN 'scheduled' ELSE status END, "
                "completed_at = NULL, updated_at = now() "
                "WHERE id = $1 AND status IN ('scheduled','paused','completed') RETURNING *",
                subscription_id, int(add_count), add_price)
    return await repo.get_subscription(int(upd["id"]))


async def update_sub(subscription_id: int, *, name: str | None = None, send_time: str | None = None,
                     total_items: int | None = None, note: str | None = None,
                     package_title: str | None = None) -> dict:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        raise ValueError("الاشتراك غير موجود")
    fields: dict[str, Any] = {}
    if name is not None:
        fields["customer_name"] = str(name).strip()[:100]
    if package_title is not None:
        fields["package_title"] = str(package_title).strip()[:100]
    if send_time is not None:
        fields["send_time"] = _time_value(send_time).strftime("%H:%M")
    if total_items is not None:
        n = int(total_items)
        if n < int(sub.get("sent_items") or 0):
            raise ValueError("العدد لا يقل عن التسليمات المنفذة")
        if not 1 <= n <= 730:
            raise ValueError("العدد بين 1 و730")
        fields["total_items"] = n
    if note is not None:
        fields["note"] = str(note)[:300]
    if "send_time" in fields and sub["status"] == "scheduled" and not sub.get("missed_slot"):
        fields["next_send_at"] = first_slot(fields["send_time"], (sub.get("timezone") or sub.get("timezone_name")))
    upd = await repo.update_fields(subscription_id, **fields)
    return await repo.get_subscription(int(upd["id"]))


async def activate(subscription_id: int) -> dict | None:
    """تفعيل اشتراك «بانتظار المحتوى» ← «نشط» — يبدأ عدّ التسليم اليومي من أول موعد قادم.

    لا يُفعّل إلا وفي الطابور زوج جاهز، حتى لا تبدأ الساعة على طابور فارغ.
    """
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        raise ValueError("الاشتراك غير موجود")
    if sub["status"] == "scheduled":
        return sub
    if sub["status"] != "awaiting_assets":
        raise ValueError("لا يمكن تفعيل هذا الاشتراك — حالته لا تسمح")
    ready = int(sub.get("ready_items") or 0)
    if ready < 1:
        if int(sub.get("sent_items") or 0) > 0:
            raise ValueError("نفدت الأزواج الجاهزة — أضف أزواجاً جديدة (🖼️+✍️) ثم فعّل")
        raise ValueError("ارفع زوجاً واحداً على الأقل (🖼️ تصميم + ✍️ نصه) قبل التفعيل")
    upd = await repo.set_status(subscription_id, "scheduled", expect=("awaiting_assets",))
    if not upd:
        raise ValueError("تغيّرت الحالة أثناء التفعيل — أعد المحاولة")
    nxt = first_slot(str(upd.get("send_time") or "20:00")[:5], (upd.get("timezone") or upd.get("timezone_name")))
    await repo.update_fields(subscription_id, next_send_at=nxt)
    return await repo.get_subscription(subscription_id)


# ───────────────── الإيقاف/الإلغاء ─────────────────

async def pause(subscription_id: int) -> dict | None:
    sub = await repo.get_subscription(subscription_id)
    if not sub or sub["status"] not in ("scheduled", "awaiting_assets"):
        return None
    return await repo.set_status(subscription_id, "paused", expect=("scheduled", "awaiting_assets"))


async def resume(subscription_id: int) -> dict | None:
    sub = await repo.get_subscription(subscription_id)
    if not sub or sub["status"] != "paused":
        return None
    fields = {}
    nsa = sub.get("next_send_at")
    if nsa is None or nsa <= datetime.now(timezone.utc):
        # موعد جديد قادم إن كان الموعد قد فات (أو بلا موعد)
        fields["next_send_at"] = first_slot(str(sub.get("send_time") or "20:00")[:5], (sub.get("timezone") or sub.get("timezone_name")))
    if fields:
        await repo.update_fields(subscription_id, **fields)
    return await repo.set_status(subscription_id, "scheduled", expect=("paused",))


def refund_quote(sub: dict) -> Decimal:
    """مبلغ الاسترداد النسبي = السعر × المتبقٍ/العدد — بالتساوي عن كل تسليم غير مسلَّم.

    الحالات المنهية (مكتمل/ملغي/مسترد) = صفر — لا ازدواج استرداد.
    """
    if str(sub.get("status") or "") in ("completed", "cancelled", "refunded"):
        return Decimal("0")
    total = int(sub.get("total_items") or 0)
    sent = int(sub.get("sent_items") or sub.get("sent_count") or 0)
    remaining = max(0, total - sent)
    price = Decimal(sub.get("price_usd") or 0)
    if total <= 0:
        return Decimal("0")
    return money_amount(price * Decimal(remaining) / Decimal(total))


async def cancel(subscription_id: int, admin_id: int | None = None) -> tuple[dict | None, Decimal]:
    """إلغاء + استرداد نسبي (للمدفوع بالبوت فقط) — مع تراكم refunded_usd على الطلبات المرتبطة.

    يعيد (الاشتراك, المبلغ المسترد). الاشتراك اليدوي = استرداد 0 (المال برّا البوت).
    """
    async with db.pool().acquire() as c:
        async with c.transaction():
            sub = await c.fetchrow(
                "SELECT * FROM scheduled_subscriptions WHERE id=$1 FOR UPDATE", subscription_id)
            if not sub:
                return None, Decimal("0")
            if sub["status"] in ("completed", "cancelled", "refunded"):
                return dict(sub), Decimal("0")
            refund = Decimal("0")
            user_id = sub.get("user_id")
            if user_id and not sub.get("is_manual"):
                await money.lock_user(c, int(user_id))
                sent = int(await c.fetchval(
                    "SELECT count(*) FROM scheduled_subscription_items "
                    "WHERE subscription_id=$1 AND status IN ('sent','sending')", subscription_id) or 0)
                total = int(sub["total_items"])
                remaining = max(0, total - sent)
                price = Decimal(sub["price_usd"] or 0)
                refund = money_amount(price * Decimal(remaining) / Decimal(total)) if total else Decimal("0")
                if refund > 0:
                    await c.execute(
                        "INSERT INTO ledger (user_id,type,amount_usd,ref_type,ref_id,note,admin_id) "
                        "VALUES ($1,'refund',$2,'subscription',$3,$4,$5)",
                        user_id, refund, subscription_id,
                        f"استرداد SUB-{subscription_id} — {remaining}/{total} غير منفّذ", admin_id)
                    await c.execute(
                        "UPDATE users SET balance_usd=balance_usd+$2 WHERE tg_id=$1", user_id, refund)
                # توزيع الاسترداد على الطلبات المرتبطة (تراكمي مع سقف السعر — لا ازدواج)
                left = refund
                orders = await c.fetch(
                    "SELECT id, price_usd, refunded_usd FROM orders "
                    "WHERE scheduled_subscription_id=$1 ORDER BY id DESC", subscription_id)
                for o in orders:
                    if left <= 0:
                        break
                    cap = Decimal(o["price_usd"] or 0) - Decimal(o["refunded_usd"] or 0)
                    take = min(cap, left)
                    if take > 0:
                        await c.execute(
                            "UPDATE orders SET refunded_usd = refunded_usd + $2, updated_at = now() WHERE id=$1",
                            o["id"], take)
                        left -= take
                await c.execute(
                    "UPDATE orders SET status='refunded', note=$2, updated_at=now() "
                    "WHERE scheduled_subscription_id=$1 AND status <> 'refunded'",
                    subscription_id, f"إلغاء اشتراك تصميم SUB-{subscription_id}")
            await c.execute(
                "UPDATE scheduled_subscription_items SET status='skipped', updated_at=now() "
                "WHERE subscription_id=$1 AND status IN ('pending','sending','failed')", subscription_id)
            note = (f"استُرد {refund}$ — المنفّذ {int(sub['sent_count'] or 0)}/{int(sub['total_items'])}"
                    if refund > 0 else "اشتراك يدوي — لا مال بالبوت")
            await c.execute(
                "UPDATE scheduled_subscriptions SET status='refunded', missed_slot=FALSE, next_send_at=NULL, "
                "last_error=$2, updated_at=now() WHERE id=$1 AND status NOT IN ('refunded','cancelled')",
                subscription_id, note)
    return await repo.get_subscription(subscription_id), refund


# ───────────────── الأزواج (الطابور) ─────────────────

def _check_copy(copy_text: str, require: bool) -> str:
    text = (copy_text or "").strip()
    if len(text) > COPY_MAX:
        raise ValueError(f"النص طويل ({len(text)} حرف) — الحد {COPY_MAX} حرف")
    if require and not text:
        raise ValueError("النص الكتابي مطلوب لهذه الباقة")
    return text


async def add_pair(subscription_id: int, file_kind: str, file_id: str, copy_text: str = "") -> tuple[dict, str | None]:
    """زوج جديد في الطابور — يعيد (الزوج, تنبيه احتياطي أو None).

    إذا فات الموعد والطابور كان فارغاً: يُضبط إرسال فوري (اعتذار) — misseds_slot.
    """
    sub = await repo.get_subscription(subscription_id)
    if not sub or sub["status"] not in ("scheduled", "paused", "awaiting_assets"):
        raise ValueError("الاشتراك غير نشط")
    if file_kind not in ("photo", "document", "video") or not file_id:
        raise ValueError("ملف التصميم غير صالح (صورة/وثيقة/فيديو)")
    require_copy = True
    text = _check_copy(copy_text, require_copy)
    ready_before = int(sub.get("ready_items") or 0)
    total = int(sub["total_items"])
    sent = int(sub.get("sent_items") or 0)
    if ready_before + sent >= total:
        raise ValueError(f"الطابور مكتمل ({total} تسليم) — مدّد العدد أولاً إن أردت المزيد")
    item = await repo.add_pair(subscription_id, file_kind, file_id, text)
    alert = None
    fields: dict[str, Any] = {}
    now = datetime.now(timezone.utc)
    if sub["status"] == "scheduled":
        nsa = sub.get("next_send_at")
        if nsa is None:
            fields["next_send_at"] = first_slot(str(sub.get("send_time") or "20:00")[:5], (sub.get("timezone") or sub.get("timezone_name")), now)
        elif nsa <= now:
            # فات الموعد والطابور كان فارغاً ← لحاق فوري مع اعتذار
            fields["missed_slot"] = True
            fields["next_send_at"] = now
    ready_after = ready_before + 1
    if buffer_state(ready_after) == "low" and not sub.get("buffer_alert_sent"):
        fields["buffer_alert_sent"] = True
        name = sub.get("customer_name") or sub.get("target_title") or f"SUB-{sub['id']}"
        alert = (f"🚨 <b>احتياطي منخفض</b>\nSUB-{sub['id']} · {name}\n"
                 f"جاهز الآن: {ready_after} فقط — أرسل دفعة جديدة!")
    elif buffer_state(ready_after) != "low":
        fields["buffer_alert_sent"] = False
    if fields:
        await repo.update_fields(subscription_id, **fields)
    return item, alert


async def set_pair_text(subscription_id: int, seq: int, copy_text: str) -> dict:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        raise ValueError("الاشتراك غير موجود")
    text = _check_copy(copy_text, True)
    upd = await repo.set_pair_text(subscription_id, seq, text)
    if not upd:
        raise ValueError("لا يمكن تعديل هذا الزوج (غير موجود أو أُرسل بالفعل)")
    return upd


async def save_pair(subscription_id: int, seq: int, *, copy: str | None = None,
                    photo: tuple[str, str] | None = None) -> tuple[dict | None, str | None]:
    """حفظ زوج من لوحة التحكم (upsert): نص و/أو صورة بمسلسل صريح. يعيد (الزوج, خطأ|None)."""
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        return None, "الاشتراك غير موجود"
    if not 1 <= int(seq) <= 365:
        return None, "رقم التسلسل خارج الحدود"
    if copy is None and photo is None:
        return None, "لا شيء للحفظ"
    text = _check_copy(copy or "", photo is not None) if copy is not None else None
    if photo is not None and photo[0] not in ("photo", "document", "video"):
        return None, "نوع الملف غير صالح"
    item = await repo.get_item(subscription_id, int(seq))
    if item is None:
        kind, file_id = photo or ("photo", "")
        item = await repo.put_item(subscription_id, int(seq), kind, file_id, text or "")
    else:
        if item.get("status") == "sent":
            return None, "هذا الزوج سُلّم بالفعل — لا يُعدَّل"
        fields: dict[str, Any] = {}
        if text is not None:
            fields["copy_text"] = text
        if photo is not None:
            fields["file_kind"], fields["file_id"] = photo[0], photo[1]
        upd = await repo.put_item(subscription_id, int(seq),
                                  fields.get("file_kind", item.get("file_kind") or "photo"),
                                  fields.get("file_id", item.get("file_id") or ""),
                                  fields.get("copy_text", item.get("copy_text") or ""))
        item = upd or item
    if item is None:
        return None, "تعذر حفظ الزوج"
    return item, None


async def pair_photo_bytes(bot, subscription_id: int, seq: int):
    """تنزيل صورة الزوج من تيليغرام لمعاينتها في لوحة التحكم.

    يعيد (بايتات|None, اسم_ملف, نوع_المحتوى).
    """
    item = await repo.get_item(subscription_id, int(seq))
    if not item or not item.get("file_id"):
        return None, "design", "application/octet-stream"
    kind = str(item.get("file_kind") or "photo")
    ext = {"photo": "jpg", "document": "bin", "video": "mp4"}.get(kind, "bin")
    ctype = {"photo": "image/jpeg", "video": "video/mp4"}.get(kind, "application/octet-stream")
    try:
        tg_file = await bot.get_file(item["file_id"])
        buf = io.BytesIO()
        await bot.download_file(tg_file.file_path, buf)
        return buf.getvalue(), f"sub-{subscription_id}-{seq}.{ext}", ctype
    except Exception:  # noqa: BLE001
        return None, f"sub-{subscription_id}-{seq}.{ext}", ctype


async def delete_sent_message(bot, subscription_id: int, seq: int) -> tuple[bool, str | None]:
    """يحذف رسالة تسليم (معاينة) من وجه الاشتراك — قناة الزبون أو محادثته.

    للتجربة على قنواتك ثم التنظيف. يعيد (نجاح, خطأ|None).
    """
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        return False, "الاشتراك غير موجود"
    item = await repo.get_item(subscription_id, int(seq))
    if not item:
        return False, "الزوج غير موجود"
    mid = item.get("sent_message_id")
    if not mid:
        return False, "لا رسالة محفوظة لهذا الزوج (لم يُسلَّم أو حُذفت رسالته سابقاً)"
    target = sub.get("target_chat_id") or sub.get("user_id")
    if not target:
        return False, "لا وجه محدد لهذا الاشتراك"
    try:
        await bot.delete_message(int(target), int(mid))
    except Exception as e:  # noqa: BLE001
        return False, f"تعذر حذف الرسالة من الوجه ({str(e)[:80]})"
    await repo.set_sent_message(item["id"], None)
    return True, None


async def delete_pair(subscription_id: int, seq: int) -> bool:
    ok = await repo.delete_pair(subscription_id, seq)
    if ok:
        sub = await repo.get_subscription(subscription_id)
        if sub and buffer_state(int(sub.get("ready_items") or 0)) != "low":
            await repo.update_fields(subscription_id, buffer_alert_sent=False)
    return ok


# ───────────────── الإرسال ─────────────────

def _esc(s) -> str:
    from app.bot import texts as T
    return T.esc(str(s or ""))


async def deliver_next(bot, subscription_id: int, *, manual: bool = False) -> tuple[str, dict | None]:
    """يرسل الزوج التالي (caption) لوجه الاشتراك — أو (empty/error).

    يعيد (النتيجة, معلومات): 'sent' · 'empty' · 'error' · 'blocked' · 'skip'.
    """
    from app.services import order_notify as ON
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        return "skip", None
    if sub["status"] not in ("scheduled", "paused") and not manual:
        return "skip", sub
    item = await repo.claim_next_pair(subscription_id)
    if not item:
        return "empty", sub
    target = sub.get("target_chat_id") or sub.get("user_id")
    name = sub.get("customer_name") or sub.get("target_title") or f"SUB-{sub['id']}"
    missed = bool(sub.get("missed_slot"))
    try:
        if missed:
            try:
                await bot.send_message(int(target), "🙈 تأخرنا قليلاً ✨ تصميمك:")
            except Exception:  # noqa: BLE001
                pass
        caption = _esc(item.get("copy_text")) or None
        kind = item.get("file_kind")
        fid = item.get("file_id")
        if kind == "photo":
            msg = await bot.send_photo(int(target), fid, caption=caption)
        elif kind == "video":
            msg = await bot.send_video(int(target), fid, caption=caption)
        else:
            msg = await bot.send_document(int(target), fid, caption=caption)
    except TelegramForbiddenError as e:
        await repo.mark_failed(item["id"], subscription_id, str(e), retry=False)
        if sub.get("user_id"):
            try:
                from app.db.repo import users as users_repo
                await users_repo.mark_bot_blocked(int(sub["user_id"]), True)
            except Exception:  # noqa: BLE001
                pass
        return "blocked", sub
    except Exception as e:  # noqa: BLE001
        attempts = int(item.get("attempts") or 1)
        retry = attempts < 3
        await repo.mark_failed(item["id"], subscription_id, str(e), retry=retry)
        if not retry:
            try:
                await ON.notify_admins_text(
                    bot, f"⚠️ <b>توقفت باقة تصميم</b>\nSUB-{subscription_id} · {name}\n"
                         f"الزوج {item['seq']} — السبب: {_esc(str(e)[:200])}")
            except Exception:  # noqa: BLE001
                pass
        return "error", sub

    # نجاح — حساب الموعد التالي
    now = datetime.now(timezone.utc)
    more_ready = int(await db.fetchval(
        "SELECT count(*) FROM scheduled_subscription_items "
        "WHERE subscription_id=$1 AND status='pending'", subscription_id) or 0)
    if missed and more_ready:
        next_at, keep_missed = catchup_slot(now), True     # لحاق: زوج كل ساعة
    else:
        next_at, keep_missed = next_daily_slot(str(sub.get("send_time") or "20:00")[:5],
                                               (sub.get("timezone") or sub.get("timezone_name")), now), False
    updated = await repo.mark_sent(item["id"], subscription_id, missed_chain=keep_missed, next_send_at=next_at)
    mid = getattr(msg, "message_id", None)
    if mid is not None:
        try:
            await repo.set_sent_message(item["id"], int(mid))
        except Exception:  # noqa: BLE001
            pass
    if updated is None and sub.get("status") == "awaiting_assets":
        # إرسال معاينة قبل التفعيل: الزوج يُحسب والساعة لا تبدأ — نُحدّث العدّادات فقط
        try:
            updated = await repo.update_fields(
                subscription_id,
                sent_count=int(sub.get("sent_items") or 0) + 1,
                last_sent_at=datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001
            pass
    info = {"item": item, "sub": updated or sub, "seq": item["seq"]}
    if updated and updated.get("status") == "completed":
        try:
            await ON.notify_admins_text(
                bot, f"✅ <b>اكتملت باقة تصميم</b>\nSUB-{subscription_id} · {name} · "
                     f"{updated.get('sent_count')}/{updated.get('total_items')}")
        except Exception:  # noqa: BLE001
            pass
    return "sent", info


async def run_due(bot, limit: int = 5) -> int:
    """للحلقة الخلفية: يرسل ما حان من الاشتراكات."""
    sent = 0
    for _ in range(limit):
        row = await repo.due_subscription()
        if not row:
            break
        result, _ = await deliver_next(bot, int(row["id"]))
        if result == "sent":
            sent += 1
        elif result in ("empty", "skip"):
            await repo.update_fields(int(row["id"]), next_send_at=catchup_slot())
    return sent


# ───────────────── عروض ─────────────────

def _view(sub: dict) -> dict:
    out = dict(sub)
    total = int(sub.get("total_items") or 0)
    sent = int(sub.get("sent_items") or sub.get("sent_count") or 0)
    ready = int(sub.get("ready_items") or 0)
    out["remaining"] = max(0, total - sent)
    out["ready"] = ready
    out["buffer"] = buffer_state(ready)
    out["label"] = sub.get("customer_name") or sub.get("target_title") or sub.get("user_name") or f"SUB-{sub.get('id')}"
    return out


async def list_views(query: str = "", limit: int = 40, offset: int = 0) -> list[dict]:
    return [_view(s) for s in await repo.list_subscriptions(query, limit, offset)]


async def detail(subscription_id: int) -> dict | None:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        return None
    out = _view(sub)
    out["items"] = await repo.items(subscription_id)
    out["refund_quote"] = str(refund_quote(sub))
    return out


async def client_subscriptions(user_id: int, limit: int = 10) -> list[dict]:
    return [_view(s) for s in await repo.list_for_user(user_id, limit)]


async def snapshot() -> dict:
    return {
        "packages": await packages(include_disabled=True),
        "counts": await repo.counts(),
        "subs": await list_views("", limit=20),
    }
