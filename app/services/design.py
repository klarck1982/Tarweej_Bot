"""🎨 خدمات الكتابة والتصميم (v0.8.0) — التسعير، بناء المواصفات، ودورة حياة طلب التصميم اليدوي.

الطلب صف في orders بنوع kind = 'design' (خدمة واحدة أو باقة) — لا جدول جديد:
    submitted 🟡 جديد ← in_progress 🔵 قيد التنفيذ ← delivered 📤 سُلّم (بانتظار اعتماد العميل) ← completed ✅
                                   ↖ needs_revision ✏️ (العميل طلب تعديلاً → الأدمن يسلّم نسخة جديدة → delivered)
    rejected ❌ تعذّر التنفيذ (استرداد كامل) · cancelled 🚫 إلغاء العميل مجاناً ما دام 🟡

    compute_prices(spec)        (الأساس، سعر العميل، التكلفة=0) — من pricing.ADDONS / ADDON_BUNDLES / ADDON_VOICEOVER
    build_spec(d)               من بيانات المعالج إلى spec يُحفظ في الطلب
    start / deliver / approve / request_revision / reject / cancel_by_user     الانتقالات
    due_alerts / auto_approve_due                                               للمجدول

القواعد: التعديل الأول مجاني، ثم نسبة من سعر الخدمة (design_revision_pct من Cpanel، افتراضياً 30%) تُخصم بعد تأكيد صريح.
         صمت العميل بعد التسليم (design_approve_hours، افتراضياً 48 ساعة) = اعتماد تلقائي.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db import pool as db
from app.db.repo import events, orders as repo
from app.services import money as money_svc, pricing as P
from app.services.pricing import money

SERVICES = P.ADDON_ORDER                       # copy · design · reel · montage
MAX_MEDIA = 10
MAX_MESSAGE = 500
MAX_NOTES = 300
REVISION_HOURS = 24                             # مهلة تسليم النسخة المعدّلة
FREE_REVISIONS = 1

BUSINESS = (
    ("restaurant", "🍽️ مطعم / كافيه"), ("shop", "🛍️ متجر / منتجات"),
    ("education", "🎓 تعليم / دورات"), ("clinic", "🏥 عيادة / خدمة"),
    ("realestate", "🏠 عقارات"), ("other", "✍️ غير ذلك"),
)
LANGS = (("levant", "🗣️ شامي"), ("msa", "📖 فصحى"), ("egypt", "🇪🇬 مصري"), ("gulf", "🕌 خليجي"))
TONES = (("formal", "💼 رسمي"), ("fun", "😄 مرح"), ("hot", "🔥 عرض قوي"))
# الإضافات على الفيديو: (العنوان، مدفوعة؟)
EXTRAS = (("voiceover", "🎤 تعليق صوتي عربي (AI)", True), ("music", "🎵 موسيقى مرخّصة مجانية", False),
          ("subs", "📝 ترجمة نصية على الشاشة", False))
# الحد الأدنى للمواد (صور، فيديو) — يكفي تحقيق أحدهما
MEDIA_MIN = {"reel": (5, 3), "montage": (0, 1), "design": (0, 0)}

STATUS_NAME = {
    "submitted": "جديد — في الطابور", "in_progress": "قيد التنفيذ", "needs_revision": "التعديل قيد التنفيذ",
    "delivered": "سُلّم — بانتظار اعتمادك", "completed": "مكتمل", "rejected": "تعذّر التنفيذ — مُسترد",
    "cancelled": "ألغيته — مُسترد", "refunded": "مُسترد",
}
ADMIN_STATUS_NAME = {
    "submitted": "جديد", "in_progress": "قيد العمل", "needs_revision": "طلب تعديل — بانتظار تسليمك",
    "delivered": "سُلّم — بانتظار اعتماد العميل", "completed": "مكتمل", "rejected": "تعذّر — مُسترد", "cancelled": "ألغاه العميل",
}
WORKING = ("submitted", "in_progress", "needs_revision")      # ينتظر يد الأدمن
OPEN = WORKING + ("delivered",)


def label(code: str, table) -> str:
    for c, name, *_ in table:
        if c == code:
            return name
    return code


def svc_title(code: str) -> str:
    a = P.ADDONS.get(code)
    return f"{a['emoji']} {a['title']}" if a else code


def items_title(items: list[str] | tuple[str, ...]) -> str:
    """«نص + صورتان + مونتاج» من قائمة العناصر."""
    counts: dict[str, int] = {}
    for c in items:
        counts[c] = counts.get(c, 0) + 1
    parts = []
    for c, n in counts.items():
        a = P.ADDONS.get(c)
        t = a["title"] if a else c
        parts.append(f"{t} ×{n}" if n > 1 else t)
    return " + ".join(parts)


def needs_media(items) -> bool:
    return any(c != "copy" for c in items)


def has_video(items) -> bool:
    return any(c in ("reel", "montage") for c in items)


def media_min(items) -> tuple[int, int]:
    """أعلى حد أدنى بين عناصر الطلب (صور، فيديو)."""
    ph = vd = 0
    for c in items:
        p, v = MEDIA_MIN.get(c, (0, 0))
        ph, vd = max(ph, p), max(vd, v)
    return ph, vd


def media_ok(items, photos: int, videos: int) -> bool:
    ph, vd = media_min(items)
    if ph == 0 and vd == 0:
        return True
    return (ph and photos >= ph) or (vd and videos >= vd)


def deliver_hours(items) -> int:
    return max((int(P.ADDONS[c]["hours"]) for c in items if c in P.ADDONS), default=24)


# ───────────── التسعير ─────────────

def base_price(spec: dict) -> Decimal:
    if spec.get("bundle_idx") is not None:
        i = int(spec["bundle_idx"])
        if 0 <= i < len(P.ADDON_BUNDLES):
            return money(P.ADDON_BUNDLES[i]["price"])
    return money(sum((P.ADDONS[c]["price"] for c in spec.get("items") or () if c in P.ADDONS), Decimal("0")))


def compute_prices(spec: dict) -> tuple[Decimal, Decimal, Decimal]:
    """(سعر الأساس، سعر العميل مع الإضافات، تكلفتنا=0 — التنفيذ بيدنا)."""
    base = base_price(spec)
    price = base
    if "voiceover" in (spec.get("extras") or []) and has_video(spec.get("items") or ()):
        price += P.ADDON_VOICEOVER
    return base, money(price), Decimal("0.00")


def quote_from_state(d: dict) -> Decimal:
    return compute_prices({"items": d.get("items") or [], "bundle_idx": d.get("bundle_idx"), "extras": d.get("extras") or []})[1]


# ───────────── بناء المواصفات ─────────────

def build_spec(d: dict) -> dict:
    items = list(d.get("items") or [])
    media = d.get("media") or []
    kinds = [m[0] for m in media]
    brand = d.get("brand") or {}
    return {
        "kind": "design", "items": items, "bundle_idx": d.get("bundle_idx"),
        "title": items_title(items) if d.get("bundle_idx") is not None else svc_title(items[0]) if items else "تصميم",
        "business": d.get("business") or "other", "message": (d.get("message") or "").strip(),
        "media_n": len(media), "media_photos": kinds.count("photo"), "media_videos": kinds.count("video") + kinds.count("document"),
        "no_media": bool(d.get("no_media")),
        "brand": {"logo": brand.get("logo"), "colors": brand.get("colors"), "saved": bool(brand.get("saved"))},
        "lang": d.get("lang") or "levant", "tone": d.get("tone") or "fun",
        "extras": [x for x in (d.get("extras") or []) if has_video(items)],
        "notes": (d.get("notes") or "").strip() or None,
        "deliver_hours": deliver_hours(items),
    }


def spec_lines(spec: dict, esc) -> list[str]:
    """أسطر الملخص المشتركة بين شاشة العميل وبطاقة الأدمن."""
    lines = [f"🎨 الخدمة: <b>{esc(spec.get('title'))}</b>",
             f"🏷️ النشاط: {label(spec.get('business', 'other'), BUSINESS)}",
             f"✍️ الرسالة: <i>{esc(spec.get('message'))}</i>"]
    items = spec.get("items") or []
    if needs_media(items):
        if spec.get("no_media"):
            m = "بلا مواد — نستخدم صوراً جاهزة"
        else:
            parts = []
            if spec.get("media_photos"):
                parts.append(f"{spec['media_photos']} صورة")
            if spec.get("media_videos"):
                parts.append(f"{spec['media_videos']} فيديو")
            m = " + ".join(parts) or "—"
        lines.append(f"📎 المواد: {m}")
    b = spec.get("brand") or {}
    if b.get("logo") or b.get("colors"):
        bk = " + ".join(x for x in ("لوغو" if b.get("logo") else "", f"«{esc(b['colors'])}»" if b.get("colors") else "") if x)
        lines.append(f"🎨 الهوية: {bk}")
    else:
        lines.append("🎨 الهوية: بلا لوغو — يختار الفريق")
    lines.append(f"🗣️ اللغة: {label(spec.get('lang', 'levant'), LANGS)} — {label(spec.get('tone', 'fun'), TONES)}")
    if has_video(items):
        ex = [label(x, EXTRAS) for x in (spec.get("extras") or [])]
        lines.append("🎬 إضافات الفيديو: " + ("، ".join(ex) if ex else "—"))
    if spec.get("notes"):
        lines.append(f"📝 ملاحظات: <i>{esc(spec['notes'])}</i>")
    return lines


# ───────────── أدوات الوقت ─────────────

def hours_left(dt: datetime | None) -> int | None:
    if not dt:
        return None
    return int((dt - datetime.now(timezone.utc)).total_seconds() // 3600)


def left_label(dt: datetime | None) -> str:
    h = hours_left(dt)
    if h is None:
        return "—"
    if h < 0:
        return f"متأخر {abs(h)} س" if abs(h) < 48 else f"متأخر {abs(h) // 24} يوم"
    if h < 1:
        return "أقل من ساعة"
    return f"متبقٍ {h} س" if h < 48 else f"متبقٍ {h // 24} يوم"


def urgency(order: dict) -> str:
    """🔴 متأخر · 🟠 أقل من ربع المهلة · '' عادي."""
    due = order.get("due_at")
    if not due or order["status"] not in WORKING:
        return ""
    now = datetime.now(timezone.utc)
    if due < now:
        return "🔴"
    start = order.get("paid_at") or order.get("created_at") or now
    total = (due - start).total_seconds() or 1
    return "🟠" if (due - now).total_seconds() <= total * 0.25 else ""


# ───────────── الانتقالات ─────────────

async def _get(order_id: int, statuses) -> dict | None:
    o = await repo.get(order_id)
    if not o or o.get("kind") != "design" or o["status"] not in statuses:
        return None
    return o


async def start(order_id: int, admin_id: int) -> dict | None:
    """▶️ بدأت العمل: جديد → قيد التنفيذ (يمنع إلغاء العميل المجاني)."""
    o = await _get(order_id, ("submitted",))
    if not o:
        return None
    upd = await repo.update(order_id, status="in_progress", admin_id=admin_id, started_at=datetime.now(timezone.utc))
    await events.log_event("order_status", o["user_id"], order_id, from_="submitted", to="in_progress", admin_id=admin_id)
    return upd


def approve_hours() -> int:
    from app.services import cpanel as CP
    try:
        return max(1, int(CP.rt("design_approve_hours") or 48))
    except (TypeError, ValueError):
        return 48


def pct_label(p: Decimal | None = None) -> str:
    """«30» أو «12.5» — بلا صيغة علمية."""
    p = revision_pct() if p is None else p
    return str(int(p)) if p == p.to_integral() else f"{p.normalize():f}"


def revision_pct() -> Decimal:
    from app.services import cpanel as CP
    try:
        return Decimal(str(CP.rt("design_revision_pct") or "30"))
    except Exception:  # noqa: BLE001
        return Decimal("30")


async def deliver(order_id: int, admin_id: int, files: list, text: str | None) -> dict | None:
    """📤 تسليم: جديد/قيد التنفيذ/تعديل → سُلّم. يحفظ التسليم في delivery ويضبط مهلة الاعتماد التلقائي."""
    o = await _get(order_id, WORKING)
    if not o:
        return None
    now = datetime.now(timezone.utc)
    deliveries = list(o.get("delivery") or [])
    deliveries.append({"n": len(deliveries) + 1, "files": [list(f) for f in files], "text": (text or "")[:1000],
                       "at": now.isoformat(), "by": admin_id})
    upd = await repo.update(order_id, status="delivered", delivered_at=now, approve_by=now + timedelta(hours=approve_hours()),
                            delivery=deliveries, admin_id=admin_id, due_warned_at=None, late_warned_at=None)
    await events.log_event("order_status", o["user_id"], order_id, from_=o["status"], to="delivered", admin_id=admin_id,
                           files=len(files))
    return upd


async def approve(order_id: int, user_id: int | None, auto: bool = False, admin_id: int | None = None) -> dict | None:
    """✅ اعتماد: سُلّم → مكتمل (من العميل، أو تلقائياً، أو الأدمن بالنيابة)."""
    o = await _get(order_id, ("delivered",))
    if not o or (user_id is not None and o["user_id"] != user_id):
        return None
    now = datetime.now(timezone.utc)
    fields: dict = dict(status="completed", completed_at=now, approve_by=None)
    if admin_id:
        fields["admin_id"] = admin_id
    upd = await repo.update(order_id, **fields)
    await events.log_event("order_status", o["user_id"], order_id, from_="delivered", to="completed", auto=auto, admin_id=admin_id)
    return upd


def revision_fee(order: dict) -> Decimal:
    """رسم التعديل الإضافي = نسبة من سعر الخدمة الأصلي (بلا الإضافات المدفوعة لاحقاً)."""
    base = Decimal(str((order.get("spec") or {}).get("price") or order["price_usd"]))
    return money(base * revision_pct() / 100)


def next_revision_is_free(order: dict) -> bool:
    return int(order.get("revision_count") or 0) < FREE_REVISIONS


async def request_revision(order_id: int, user_id: int, text: str) -> tuple[dict | None, Decimal]:
    """✏️ طلب تعديل من العميل: سُلّم → تعديل. الأول مجاني؛ التالي يُخصم رسمه في نفس المعاملة (InsufficientBalance إن لم يكفِ).

    يعيد (الطلب، الرسم المخصوم)."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            row = await c.fetchrow("SELECT * FROM orders WHERE id = $1 AND user_id = $2 AND kind = 'design' FOR UPDATE", order_id, user_id)
            if not row or row["status"] != "delivered":
                return None, Decimal("0")
            o = repo.row_to_dict(row)
            fee = Decimal("0")
            if not next_revision_is_free(o):
                fee = revision_fee(o)
                if fee > 0:
                    await money_svc.debit(user_id, fee, "order_charge", ref_type="order", ref_id=order_id,
                                          note=f"ORD-{order_id} تعديل إضافي", conn=c)
            now = datetime.now(timezone.utc)
            await c.execute(
                "UPDATE orders SET status = 'needs_revision', revision_note = $2, revision_count = revision_count + 1, "
                "price_usd = price_usd + $3, due_at = $4, approve_by = NULL, due_warned_at = NULL, late_warned_at = NULL, "
                "updated_at = now() WHERE id = $1",
                order_id, text[:500], fee, now + timedelta(hours=REVISION_HOURS),
            )
    await events.log_event("order_revision", user_id, order_id, fee=str(fee))
    return await repo.get(order_id), fee


async def reject(order_id: int, admin_id: int, reason: str) -> dict | None:
    """❌ تعذّر التنفيذ → استرداد كامل (يشمل رسوم التعديل إن وُجدت)."""
    from app.services import orders as orders_svc
    o = await _get(order_id, OPEN)
    if not o:
        return None
    return await orders_svc.refund(order_id, reason=reason or "تعذّر تنفيذ الطلب — أُعيد المبلغ كاملاً", new_status="rejected",
                                   admin_id=admin_id)


async def cancel_by_user(order_id: int, user_id: int) -> dict | None:
    """🚫 إلغاء العميل مجاناً — فقط ما دام 🟡 جديداً (لم يبدأ العمل)."""
    from app.services import orders as orders_svc
    o = await repo.get(order_id)
    if not o or o["user_id"] != user_id or o.get("kind") != "design" or o["status"] != "submitted":
        return None
    return await orders_svc.refund(order_id, reason="ألغى العميل الطلب قبل بدء العمل — استرداد كامل", new_status="cancelled")


async def save_brand_kit(user_id: int, logo: str | None, colors: str | None) -> None:
    kit = {}
    if logo:
        kit["logo"] = logo
    if colors:
        kit["colors"] = colors[:120]
    if kit:
        await db.execute("UPDATE users SET brand_kit = brand_kit || $2::jsonb WHERE tg_id = $1", user_id, json.dumps(kit, ensure_ascii=False))


async def brand_kit(user_id: int) -> dict:
    raw = await db.fetchval("SELECT brand_kit FROM users WHERE tg_id = $1", user_id)
    if not raw:
        return {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def last_delivery(order: dict) -> dict | None:
    d = order.get("delivery") or []
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except json.JSONDecodeError:
            d = []
    return d[-1] if d else None


# ───────────── المجدول ─────────────

async def due_alerts(limit: int = 20) -> tuple[list[dict], list[dict]]:
    """(اقترب موعدها — بقي ≤ 25% من المهلة، متأخرة) — كل واحدة مرة واحدة فقط."""
    soon_rows = await db.fetch(
        "UPDATE orders SET due_warned_at = now() WHERE id IN ("
        "  SELECT id FROM orders WHERE kind = 'design' AND status = ANY($1::text[]) AND due_at IS NOT NULL AND due_warned_at IS NULL "
        "  AND due_at > now() AND (due_at - now()) <= (due_at - COALESCE(paid_at, created_at)) * 0.25 ORDER BY due_at LIMIT $2"
        ") RETURNING id", list(WORKING), limit)
    late_rows = await db.fetch(
        "UPDATE orders SET late_warned_at = now() WHERE id IN ("
        "  SELECT id FROM orders WHERE kind = 'design' AND status = ANY($1::text[]) AND due_at IS NOT NULL AND late_warned_at IS NULL "
        "  AND due_at <= now() ORDER BY due_at LIMIT $2"
        ") RETURNING id", list(WORKING), limit)
    soon = [o for r in soon_rows if (o := await repo.get(int(r["id"])))]
    late = [o for r in late_rows if (o := await repo.get(int(r["id"])))]
    return soon, late


async def auto_approve_due(limit: int = 20) -> list[dict]:
    rows = await db.fetch(
        "SELECT id FROM orders WHERE kind = 'design' AND status = 'delivered' AND approve_by IS NOT NULL AND approve_by <= now() "
        "ORDER BY approve_by LIMIT $1", limit)
    out = []
    for r in rows:
        upd = await approve(int(r["id"]), None, auto=True)
        if upd:
            out.append(upd)
    return out


async def count_working() -> int:
    return int(await db.fetchval("SELECT count(*) FROM orders WHERE kind = 'design' AND status = ANY($1::text[])", list(WORKING)) or 0)


async def count_late() -> int:
    return int(await db.fetchval(
        "SELECT count(*) FROM orders WHERE kind = 'design' AND status = ANY($1::text[]) AND due_at < now()", list(WORKING)) or 0)
