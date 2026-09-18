"""📝 القنوات الشريكة — التسعير، التحقق، ودورة حياة طلب النشر.

    quote(channel, fmt)              (سعر العميل، تكلفتنا) لصيغة معيّنة — من أسعار القناة × المضاعف الحي
    formats_for(channel)             الصيغ المتاحة لهذه القناة مع أسعارها
    validate_channel(raw)            تنظيف نموذج Cpanel (يرمي ValueError بالعربية)
    schedule / publish / finish      انتقالات الأدمن (بطاقة الطلب)
    cancel_by_user                   إلغاء العميل مجاناً ما دام الطلب 🟡 جديداً
    finish_due / reminders_due       للمجدول: إنهاء تلقائي بعد 24/48 ساعة + تذكير قبل الموعد بساعة

الحالات (نفس أعمدة orders): submitted 🟡 جديد ← in_progress 📅 مجدول ← active 🟢 منشور ← completed ✅ انتهى
                             rejected ❌ (استرداد كامل) · cancelled 🚫 (إلغاء العميل قبل الجدولة — استرداد كامل)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.db import pool as db
from app.db.repo import events, orders as repo, partner_channels as PC
from app.services import pricing as P
from app.services.pricing import money

FORMATS = {
    "24h": ("🕐", "منشور 24 ساعة", 24),
    "48h": ("🕑", "منشور 48 ساعة", 48),
    "pin": ("📌", "مثبَّت 24 ساعة", 24),
}
FORMAT_ORDER = ("24h", "48h", "pin")
STATUS_NAME = {
    "submitted": "جديد — بانتظار تأكيد الموعد", "in_progress": "مجدول", "active": "منشور الآن",
    "completed": "انتهى", "rejected": "تعذّر النشر — مُسترد", "cancelled": "ألغيته — مُسترد",
}
STATUS_ICON = {"in_progress": "📅"}   # الباقي من orders_svc.STATUS_ICON

_TME_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(\+[A-Za-z0-9_-]{6,}|joinchat/[A-Za-z0-9_-]{6,}|[A-Za-z][A-Za-z0-9_]{3,31})/?$", re.I)
_AT_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


def fmt_label(code: str) -> str:
    e, name, _ = FORMATS[code]
    return f"{e} {name}"


def fmt_hours(code: str) -> int:
    return FORMATS[code][2]


# ───────────── التسعير ─────────────

def channel_cost(ch: dict, code: str) -> Decimal | None:
    """ما ندفعه لصاحب القناة لهذه الصيغة — None إن لم تكن متاحة."""
    base = money(Decimal(str(ch["price_24h"])))
    if code == "24h":
        return base
    if code == "48h":
        return money(Decimal(str(ch["price_48h"]))) if ch.get("price_48h") is not None else None
    if code == "pin":
        if not ch.get("allow_pin", True):
            return None
        if ch.get("price_pin") is not None:
            return money(Decimal(str(ch["price_pin"])))
        return money(base * (1 + P.TG_POST_PIN_EXTRA))
    return None


def quote(ch: dict, code: str) -> tuple[Decimal, Decimal] | None:
    """(سعر العميل، تكلفتنا). التثبيت بسعر خاص = تكلفة × المضاعف؛ بلا سعر خاص = سعر 24 س × المضاعف × (1 + الزيادة)."""
    cost = channel_cost(ch, code)
    if cost is None:
        return None
    return money(cost * P.TG_POST_MULT), cost


def formats_for(ch: dict) -> list[tuple[str, Decimal, Decimal]]:
    out = []
    for code in FORMAT_ORDER:
        q = quote(ch, code)
        if q:
            out.append((code, q[0], q[1]))
    return out


def subs_label(n: int | None) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}".rstrip("0").rstrip(".") + " مليون"
    if n >= 1000:
        v = n / 1000
        return f"{v:.1f}".rstrip("0").rstrip(".") + " ألف"
    return str(n)


def clean_url(raw: str) -> tuple[str, str | None]:
    """يعيد (url موحّد, username أو None). يقبل @name · t.me/name · https://t.me/+invite."""
    s = (raw or "").strip()
    m = _TME_RE.match(s)
    if m:
        tail = m.group(1)
        if tail.startswith("+") or tail.lower().startswith("joinchat/"):
            return f"https://t.me/{tail}", None
        return f"https://t.me/{tail}", tail
    m = _AT_RE.match(s)
    if m:
        return f"https://t.me/{m.group(1)}", m.group(1)
    raise ValueError("رابط القناة: اكتب @اسم_القناة أو t.me/اسم_القناة (أو رابط دعوة t.me/+…)")


def _price(v, label: str, required: bool) -> Decimal | None:
    if v in (None, ""):
        if required:
            raise ValueError(f"{label}: مطلوب")
        return None
    try:
        d = money(Decimal(str(v).replace("،", ".").replace(",", ".").strip()))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: رقم غير صالح") from None
    if not Decimal("0.5") <= d <= Decimal("5000"):
        raise ValueError(f"{label}: بين 0.50$ و 5000$")
    return d


def validate_channel(raw: dict) -> dict:
    """تنظيف نموذج القناة من Cpanel — يرمي ValueError بنص عربي واضح."""
    title = " ".join(str(raw.get("title") or "").split())[:40]
    if len(title) < 2:
        raise ValueError("اسم القناة: حرفان على الأقل")
    url, username = clean_url(str(raw.get("url") or ""))
    category = str(raw.get("category") or "general")
    if category not in PC.CATEGORIES:
        raise ValueError("الفئة غير معروفة")
    try:
        subscribers = int(str(raw.get("subscribers") or 0).replace(",", "").strip() or 0)
        avg_views = raw.get("avg_views")
        avg_views = int(str(avg_views).replace(",", "").strip()) if avg_views not in (None, "") else None
    except ValueError:
        raise ValueError("المشتركون/المشاهدات: أرقام صحيحة فقط") from None
    if not 0 <= subscribers <= 50_000_000 or (avg_views is not None and not 0 <= avg_views <= 50_000_000):
        raise ValueError("المشتركون/المشاهدات: رقم غير منطقي")
    return {
        "title": title, "username": username, "url": url, "category": category,
        "subscribers": subscribers, "avg_views": avg_views,
        "blurb": " ".join(str(raw.get("blurb") or "").split())[:200],
        "price_24h": _price(raw.get("price_24h"), "سعر منشور 24 ساعة", True),
        "price_48h": _price(raw.get("price_48h"), "سعر منشور 48 ساعة", False),
        "price_pin": _price(raw.get("price_pin"), "سعر المثبَّت", False),
        "allow_pin": bool(raw.get("allow_pin", True)),
        "owner_contact": str(raw.get("owner_contact") or "").strip()[:80],
        "notes": str(raw.get("notes") or "").strip()[:300],
        "enabled": bool(raw.get("enabled", True)),
        "sort_order": max(0, min(int(raw.get("sort_order") or 100), 9999)),
    }


def channel_view(ch: dict) -> dict:
    """نسخة للواجهة مع الأسعار النهائية المحسوبة (ما يدفعه العميل وربحنا لكل صيغة)."""
    out = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in ch.items()}
    out["created_at"] = ch["created_at"].isoformat() if ch.get("created_at") else None
    out["updated_at"] = ch["updated_at"].isoformat() if ch.get("updated_at") else None
    out["quotes"] = {code: {"client": str(price), "cost": str(cost), "profit": str(money(price - cost))} for code, price, cost in formats_for(ch)}
    out["category_label"] = PC.cat_label(ch["category"])
    return out


# ───────────── طلب النشر: البناء والتسعير ─────────────

def build_spec(ch: dict, fmt_code: str, text: str | None, media: list, when_text: str | None, addons: list[str], tg_username: str | None) -> dict:
    price, cost = quote(ch, fmt_code)
    return {
        "kind": "tg_post", "channel_id": ch["id"], "channel_title": ch["title"], "channel_url": ch["url"],
        "channel_subs": int(ch.get("subscribers") or 0), "format": fmt_code, "hours": fmt_hours(fmt_code),
        "text": text, "media_n": len(media or []), "when": (when_text or "").strip() or None,
        "addons": list(addons or []), "tg_username": tg_username,
        "post_price": str(price), "post_cost": str(cost),
    }


def compute_prices(spec: dict) -> tuple[Decimal, Decimal, Decimal]:
    """(الميزانية=سعر المنشور، سعر العميل مع الإضافات، التكلفة) — يُستدعى من orders_svc.compute_prices."""
    base = money(Decimal(str(spec["post_price"])))
    cost = money(Decimal(str(spec["post_cost"])))
    price = base
    if "copy" in (spec.get("addons") or []):
        price += P.ADDONS["copy"]["price"]
    return base, money(price), cost


# ───────────── انتقالات الأدمن ─────────────

async def schedule(order_id: int, admin_id: int, when: datetime) -> dict | None:
    """📅 تأكيد الموعد: جديد/مجدول → مجدول بموعد محدد."""
    o = await repo.get(order_id)
    if not o or o.get("kind") != "tg_post" or o["status"] not in ("submitted", "in_progress"):
        return None
    upd = await repo.update(order_id, status="in_progress", scheduled_at=when, reminded_at=None, admin_id=admin_id,
                            last_sync_at=datetime.now(timezone.utc))
    await events.log_event("order_status", o["user_id"], order_id, from_=o["status"], to="in_progress", admin_id=admin_id,
                           scheduled_at=when.isoformat())
    return upd


async def publish(order_id: int, admin_id: int, post_url: str) -> dict | None:
    """🔗 تم النشر: جديد/مجدول → منشور، مع حساب موعد الانتهاء (24/48 ساعة)."""
    o = await repo.get(order_id)
    if not o or o.get("kind") != "tg_post" or o["status"] not in ("submitted", "in_progress"):
        return None
    now = datetime.now(timezone.utc)
    hours = int(o["spec"].get("hours") or 24)
    upd = await repo.update(order_id, status="active", post_url=post_url, started_at=now, ends_at=now + timedelta(hours=hours),
                            admin_id=admin_id, last_sync_at=now)
    await events.log_event("order_status", o["user_id"], order_id, from_=o["status"], to="active", admin_id=admin_id)
    return upd


async def finish(order_id: int, admin_id: int | None, views: int | None = None) -> dict | None:
    """✅ انتهى: منشور → انتهى (يدوياً أو من المجدول)."""
    o = await repo.get(order_id)
    if not o or o.get("kind") != "tg_post" or o["status"] != "active":
        return None
    now = datetime.now(timezone.utc)
    fields: dict = dict(status="completed", completed_at=now, last_sync_at=now)
    if admin_id:
        fields["admin_id"] = admin_id
    if views is not None:
        fields["results"] = {**(o.get("results") or {}), "views": int(views)}
    upd = await repo.update(order_id, **fields)
    await events.log_event("order_status", o["user_id"], order_id, from_="active", to="completed", admin_id=admin_id, auto=admin_id is None)
    return upd


async def set_views(order_id: int, admin_id: int, views: int) -> dict | None:
    o = await repo.get(order_id)
    if not o or o.get("kind") != "tg_post" or o["status"] not in ("active", "completed"):
        return None
    return await repo.update(order_id, results={**(o.get("results") or {}), "views": int(views)}, admin_id=admin_id)


async def reject(order_id: int, admin_id: int, reason: str) -> dict | None:
    """❌ تعذّر النشر → استرداد كامل."""
    from app.services import orders as orders_svc
    o = await repo.get(order_id)
    if not o or o.get("kind") != "tg_post" or o["status"] not in ("submitted", "in_progress", "active"):
        return None
    return await orders_svc.refund(order_id, reason=reason or "تعذّر النشر في القناة — أُعيد المبلغ كاملاً",
                                   new_status="rejected", admin_id=admin_id)


async def cancel_by_user(order_id: int, user_id: int) -> dict | None:
    """🚫 إلغاء العميل مجاناً — فقط ما دام الطلب 🟡 جديداً (لم يُجدول بعد)."""
    from app.services import orders as orders_svc
    o = await repo.get(order_id)
    if not o or o["user_id"] != user_id or o.get("kind") != "tg_post" or o["status"] != "submitted":
        return None
    return await orders_svc.refund(order_id, reason="ألغى العميل الطلب قبل الجدولة — استرداد كامل", new_status="cancelled")


# ───────────── المجدول ─────────────

async def finish_due(limit: int = 20) -> list[dict]:
    rows = await db.fetch(
        "SELECT id FROM orders WHERE kind = 'tg_post' AND status = 'active' AND ends_at IS NOT NULL AND ends_at <= now() ORDER BY ends_at LIMIT $1", limit)
    out = []
    for r in rows:
        upd = await finish(int(r["id"]), None)
        if upd:
            out.append(upd)
    return out


async def reminders_due(minutes_before: int = 60, limit: int = 20) -> list[dict]:
    """طلبات مجدولة يبدأ موعدها خلال `minutes_before` دقيقة ولم يُذكَّر بها بعد."""
    rows = await db.fetch(
        "UPDATE orders SET reminded_at = now() WHERE id IN ("
        "  SELECT id FROM orders WHERE kind = 'tg_post' AND status = 'in_progress' AND reminded_at IS NULL "
        "  AND scheduled_at IS NOT NULL AND scheduled_at <= now() + ($1 || ' minutes')::interval ORDER BY scheduled_at LIMIT $2"
        ") RETURNING id", str(minutes_before), limit)
    out = []
    for r in rows:
        o = await repo.get(int(r["id"]))
        if o:
            out.append(o)
    return out


async def count_waiting() -> int:
    """للوحة: منشورات بانتظار الموعد أو النشر."""
    return int(await db.fetchval("SELECT count(*) FROM orders WHERE kind = 'tg_post' AND status IN ('submitted','in_progress')") or 0)


# ───────────── تفسير موعد كتبه الأدمن ─────────────

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def parse_when(raw: str, tz) -> datetime | None:
    """يفهم: «20:00» · «غداً 20:00» · «17/09 20:00» · «2026-09-17 20:00» · «بعد ساعتين» · «الآن». يعيد UTC أو None."""
    s = (raw or "").strip().translate(_AR_DIGITS).replace("،", " ").replace("  ", " ")
    now = datetime.now(tz)
    if s in ("الآن", "الان", "now"):
        return now.astimezone(timezone.utc)
    m = re.match(r"^بعد\s+(\d+)\s*(ساعة|ساعات|س|h)$", s)
    if m:
        return (now + timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)
    if s in ("بعد ساعة",):
        return (now + timedelta(hours=1)).astimezone(timezone.utc)
    if s in ("بعد ساعتين",):
        return (now + timedelta(hours=2)).astimezone(timezone.utc)
    day_off = 0
    for w, off in (("بعد غد", 2), ("بعد بكرة", 2), ("غداً", 1), ("غدا", 1), ("بكرة", 1), ("بكرا", 1), ("اليوم", 0)):
        if s.startswith(w):
            day_off = off
            s = s[len(w):].strip()
            break
    tm = re.search(r"(\d{1,2})[:.](\d{2})", s)
    if not tm:
        tm2 = re.search(r"(\d{1,2})\s*(م|ص|pm|am)?$", s)
        if not tm2:
            return None
        h, mi = int(tm2.group(1)), 0
        suf = (tm2.group(2) or "").lower()
        if suf in ("م", "pm") and h < 12:
            h += 12
        if suf in ("ص", "am") and h == 12:
            h = 0
        s = s[:tm2.start()].strip()
    else:
        h, mi = int(tm.group(1)), int(tm.group(2))
        rest = s[tm.end():].strip().lower()
        if rest in ("م", "pm", "مساء", "مساءً") and h < 12:
            h += 12
        s = s[:tm.start()].strip()
    if not 0 <= h <= 23 or not 0 <= mi <= 59:
        return None
    date = None
    dm = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if dm:
        y, mo, d = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        date = (y, mo, d)
    else:
        dm = re.match(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?$", s)
        if dm:
            d, mo = int(dm.group(1)), int(dm.group(2))
            y = int(dm.group(3)) if dm.group(3) else now.year
            y = y + 2000 if y < 100 else y
            date = (y, mo, d)
        elif s:
            return None
    try:
        if date:
            dt = datetime(date[0], date[1], date[2], h, mi, tzinfo=tz)
        else:
            dt = now.replace(hour=h, minute=mi, second=0, microsecond=0) + timedelta(days=day_off)
            if day_off == 0 and dt < now - timedelta(minutes=5):
                dt += timedelta(days=1)   # «20:00» وقد مضت اليوم = غداً
    except ValueError:
        return None
    if dt < now - timedelta(minutes=10) or dt > now + timedelta(days=60):
        return None
    return dt.astimezone(timezone.utc)
