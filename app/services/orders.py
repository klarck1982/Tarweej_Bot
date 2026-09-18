"""منطق طلبات Meta — من الملخص حتى الاكتمال (الخطوة 3: محاكاة · الخطوة 4: نور الحقيقي بنفس الشيفرة).

    confirm()           التأكيد = معاملة واحدة مقفلة: خصم + سطر دفتر + الطلب paid  (أو InsufficientBalance)
    submit()            إرسال إلى نور: POST /campaigns بـ Idempotency-Key ord-{id} ← submitted + nour_id + charged
                        فشل: insufficient_balance → يبقى paid ويُعاد بعد 30 دقيقة (مال العميل محفوظ)
                              duplicate_request   → نبحث عن الحملة بعنوانها ORD-{id}
                              خطأ آخر ×3          → failed_submit + استرداد كامل
    apply_nour_status() يطبّق حالة نور على الطلب (in_progress / active / paused / completed / rejected←استرداد)
    refund()            استرداد كامل/جزئي في معاملة واحدة (يستخدمه الرفض والأدمن)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db import pool as db
from app.db.repo import events, orders as repo, settings as settings_repo
from app.services import money as money_svc, nour, pricing as P, targeting as TG
from app.services.money import InsufficientBalance
from app.services.pricing import money

log = logging.getLogger("orders")

MAX_SUBMIT_ATTEMPTS = 3
RETRY_MINUTES_BALANCE = 30
RETRY_MINUTES_OTHER = 5

# ترجمة حالة نور → حالتنا
NOUR_TO_LOCAL = {
    "pending_admin": "submitted",
    "in_progress": "in_progress",
    "approved": "in_progress",
    "active": "active",
    "paused": "paused",
    "completed": "completed",
    "rejected": "rejected",
}
STATUS_ICON = {
    "awaiting_payment": "💤", "paid": "📨", "submitted": "🟡", "in_progress": "🔵", "active": "🟢",
    "paused": "⏸️", "needs_revision": "✏️", "delivered": "📤", "completed": "✅", "rejected": "❌", "failed_submit": "⚠️", "refunded": "↩️", "cancelled": "🚫",
}
STATUS_NAME = {
    "awaiting_payment": "بانتظار الدفع", "paid": "قيد الإرسال", "submitted": "قيد المراجعة", "in_progress": "قيد التجهيز",
    "active": "يعمل الآن", "paused": "متوقف مؤقتاً", "needs_revision": "بانتظار تعديلك", "completed": "مكتمل", "rejected": "مرفوض — مُسترد",
    "failed_submit": "تعذّر الإرسال — مُسترد", "refunded": "مُسترد", "cancelled": "ملغى",
}
# أسماء الحالات الخاصة بإعلانات تيليغرام (نفس الرموز، صياغة أدق للعميل)
TG_ADS_STATUS_NAME = {
    "submitted": "قيد المراجعة", "in_progress": "أُنشئ — بمراجعة تيليغرام", "active": "يعمل الآن",
    "needs_revision": "النص يحتاج تعديلاً", "rejected": "رفضه تيليغرام — مُسترد",
}
KIND_NAME = {"meta_campaign": "إعلان فيسبوك/إنستغرام", "tg_ads": "إعلان Telegram Ads", "tg_post": "نشر في قناة شريكة", "design": "تصميم وكتابة"}
KIND_EMOJI = {"meta_campaign": "📢", "tg_ads": "📣", "tg_post": "📝", "design": "🎨"}
# القنوات الشريكة: submitted 🟡 جديد · in_progress 📅 مجدول · active 🟢 منشور · completed ✅ انتهى
TG_POST_STATUS_NAME = {
    "submitted": "جديد — بانتظار تأكيد الموعد", "in_progress": "مجدول", "active": "منشور الآن",
    "completed": "انتهى", "rejected": "تعذّر النشر — مُسترد", "cancelled": "ألغيته — مُسترد",
}


def status_name(order: dict) -> str:
    st = order["status"]
    if order.get("kind") == "tg_ads":
        return TG_ADS_STATUS_NAME.get(st, STATUS_NAME.get(st, st))
    if order.get("kind") == "tg_post":
        return TG_POST_STATUS_NAME.get(st, STATUS_NAME.get(st, st))
    if order.get("kind") == "design":
        from app.services import design as DS
        return DS.STATUS_NAME.get(st, STATUS_NAME.get(st, st))
    return STATUS_NAME.get(st, st)


def status_icon(order: dict) -> str:
    if order.get("kind") == "tg_post" and order["status"] == "in_progress":
        return "📅"
    return STATUS_ICON.get(order["status"], "•")


def status_label(status: str) -> str:
    return f"{STATUS_ICON.get(status, '•')} {STATUS_NAME.get(status, status)}"


# ───────────── حساب السعر من المواصفات ─────────────

def compute_prices(spec: dict) -> tuple[Decimal, Decimal, Decimal]:
    """يعيد (الميزانية، سعر العميل، تكلفتنا) من spec — بما فيها الإضافات (نص إعلاني)."""
    if spec.get("kind") == "tg_ads":
        return P.tg_ads_quote(spec["budget"], copy_addon="copy" in (spec.get("addons") or []))
    if spec.get("kind") == "tg_post":
        from app.services import partner_posts
        return partner_posts.compute_prices(spec)
    if spec.get("kind") == "design":
        from app.services import design
        return design.compute_prices(spec)
    daily = Decimal(str(spec["daily"]))
    days = int(spec["days"])
    budget, price, cost = P.meta_custom_price(daily, days)
    if spec.get("price_override"):
        # حزمة بسعر ثابت (انطلاقة متجر 38$) — الإضافات مضمّنة في السعر
        return budget, money(Decimal(str(spec["price_override"]))), cost
    addons = spec.get("addons") or []
    for a in addons:
        price += P.ADDONS[a]["price"]
    return budget, money(price), cost


def build_nour_payload(order: dict, fallback_username: str) -> dict:
    """يحوّل spec إلى جسم POST /campaigns كما تريده وثائق نور (النمط A)."""
    spec = order["spec"]
    daily = Decimal(str(spec["daily"]))
    platform = spec["platform"]
    payload: dict = {
        "content_type": "manager_setup",
        "platform": platform,
        "goal": spec.get("goal", "post_promotion"),
        "duration_days": int(spec["days"]),
        "whatsapp_number": spec["whatsapp"],
        "telegram_username": spec.get("tg_username") or fallback_username,
        "targeting": {
            "countries": {spec["country"]: TG.validate_provinces(spec["country"], spec.get("provinces") or ["all"])},
            "gender": spec.get("gender", "all"),
            "age_min": int(spec.get("age_min", 18)),
            "age_max": int(spec.get("age_max", 65)),
        },
        "title": f"ORD-{order['id']}",
    }
    if platform == "both":
        half = (daily / 2).quantize(Decimal("0.01"))
        payload["budget_daily_fb"] = float(half)
        payload["budget_daily_ig"] = float(money(daily - half))
    else:
        payload["budget_daily"] = float(daily)
    return payload


# ───────────── التأكيد (الخصم) ─────────────

async def confirm(user_id: int, spec: dict, draft_id: int | None = None) -> dict:
    """يخصم السعر وينشئ الطلب paid في معاملة واحدة. يرفع InsufficientBalance بلا أي أثر إن لم يكفِ الرصيد.

    الطلبات اليدوية (tg_ads) تُنشأ مباشرة بحالة submitted — لا يوجد شريك يُرسل إليه، الأدمن ينفّذها بيده."""
    budget, price, cost = compute_prices(spec)
    spec = {**spec, "budget": str(budget), "price": str(price), "cost": str(cost)}
    kind = spec.get("kind") or "meta_campaign"
    manual = kind != "meta_campaign"
    init_status = "submitted" if manual else "paid"
    import json
    async with db.pool().acquire() as c:
        async with c.transaction():
            if draft_id:
                row = await c.fetchrow(
                    "UPDATE orders SET status = $6, kind = $7, spec = $2::jsonb, price_usd = $3, cost_usd = $4, paid_at = now(), "
                    "submitted_at = CASE WHEN $6 = 'submitted' THEN now() ELSE NULL END, "
                    "expires_at = NULL, updated_at = now(), idempotency_key = 'ord-' || id "
                    "WHERE id = $1 AND user_id = $5 AND status = 'awaiting_payment' RETURNING *",
                    draft_id, json.dumps(spec, ensure_ascii=False), price, cost, user_id, init_status, kind,
                )
            else:
                row = None
            if row is None:
                row = await c.fetchrow(
                    "INSERT INTO orders (user_id, kind, status, spec, price_usd, cost_usd, paid_at, submitted_at) "
                    "VALUES ($1, $5, $6, $2::jsonb, $3, $4, now(), CASE WHEN $6 = 'submitted' THEN now() END) RETURNING *",
                    user_id, json.dumps(spec, ensure_ascii=False), price, cost, kind, init_status,
                )
                await c.execute("UPDATE orders SET idempotency_key = 'ord-' || id WHERE id = $1", row["id"])
            if kind == "design":
                # مهلة التسليم تبدأ لحظة الدفع (ساعات الخدمة الأطول في الطلب)
                hours = int(spec.get("deliver_hours") or 24)
                await c.execute("UPDATE orders SET due_at = now() + ($2 || ' hours')::interval, started_at = NULL WHERE id = $1",
                                row["id"], str(hours))
            # الخصم — يرفع InsufficientBalance فتُلغى المعاملة كلها (الطلب لا يُنشأ)
            what = KIND_NAME.get(kind, kind) if manual else f"إعلان {TG.PLATFORM_NAME.get(spec.get('platform'), '')}"
            await money_svc.debit(user_id, price, "order_charge", ref_type="order", ref_id=row["id"],
                                  note=f"ORD-{row['id']} {what}", conn=c)
    order = await repo.get(row["id"])
    await events.log_event("order_paid", user_id, order["id"], price=str(price), cost=str(cost), kind=kind)
    return order


# ───────────── الإرسال إلى نور ─────────────

async def submit(order_id: int) -> dict:
    """يرسل الطلب إلى نور (أو المحاكاة). يعيد الطلب المحدَّث. لا يرمي استثناءات — النتيجة في status/note."""
    order = await repo.get(order_id)
    if not order or order["status"] != "paid" or order.get("kind") != "meta_campaign":
        return order
    fallback = await settings_repo.get("admin_fallback_username", "") or ""
    payload = build_nour_payload(order, fallback)
    key = order.get("idempotency_key") or f"ord-{order_id}"
    attempts = int(order.get("submit_attempts") or 0) + 1
    client = nour.client()
    try:
        res = await client.create_campaign(payload, key)
    except nour.NourError as e:
        return await _handle_submit_error(order, e, payload, attempts)
    charged = res.get("charged")
    note = None
    tol = Decimal(str(await settings_repo.get("order_charge_tolerance_usd", "0.05")))
    if charged is not None and abs(Decimal(charged) - Decimal(order["cost_usd"])) > tol:
        note = f"⚠️ فرق تكلفة: نور خصم {P.fmt(charged)} والمتوقع {P.fmt(order['cost_usd'])}"
    updated = await repo.update(
        order_id, status="submitted", nour_id=str(res["id"]), nour_status="pending_admin",
        charged_usd=money(charged) if charged is not None else None, submitted_at=datetime.now(timezone.utc),
        submit_attempts=attempts, next_retry_at=None, nour_payload=payload, nour_response=res.get("raw"),
        note=note, last_sync_at=datetime.now(timezone.utc),
    )
    await events.log_event("order_submitted", order["user_id"], order_id, nour_id=str(res["id"]),
                           charged=str(charged), dry_run=client.dry_run)
    return updated


async def _handle_submit_error(order: dict, e: nour.NourError, payload: dict, attempts: int) -> dict:
    oid = order["id"]
    now = datetime.now(timezone.utc)
    await events.log_event("order_submit_error", order["user_id"], oid, code=e.code, http=e.http, attempt=attempts)
    if e.code == "duplicate_request":
        # أُرسل سابقاً ولم نحفظ الرد — نبحث بعنوان الحملة
        found = await nour.client().find_by_title(f"ORD-{oid}")
        if found:
            return await repo.update(oid, status=NOUR_TO_LOCAL.get(found.get("status"), "submitted"),
                                     nour_id=str(found.get("id")), nour_status=found.get("status"),
                                     charged_usd=money(found.get("budget_charged")) if found.get("budget_charged") is not None else None,
                                     submitted_at=now, submit_attempts=attempts, next_retry_at=None,
                                     nour_payload=payload, nour_response=found, last_sync_at=now)
        return await repo.update(oid, submit_attempts=attempts, next_retry_at=now + timedelta(hours=6),
                                 note="duplicate_request ولم نجد الحملة — راجع لوحة نور يدوياً", nour_payload=payload)
    if e.code == "insufficient_balance":
        # رصيدنا عند نور لا يكفي — الطلب ينتظر، مال العميل محفوظ، الأدمن يشحن ثم يُعاد تلقائياً
        return await repo.update(oid, submit_attempts=attempts, next_retry_at=now + timedelta(minutes=RETRY_MINUTES_BALANCE),
                                 note=f"رصيد نور غير كافٍ (المطلوب {e.details.get('required', '?')}$ — المتاح {e.details.get('balance', '?')}$)",
                                 nour_payload=payload, nour_response={"error": e.code, "details": e.details})
    if e.code in ("unauthorized", "forbidden"):
        return await repo.update(oid, submit_attempts=attempts, next_retry_at=now + timedelta(hours=1),
                                 note=f"توكن نور مرفوض ({e.code}) — راجع NOUR_ADS_TOKEN", nour_payload=payload,
                                 nour_response={"error": e.code})
    if e.retryable and attempts < MAX_SUBMIT_ATTEMPTS:
        return await repo.update(oid, submit_attempts=attempts, next_retry_at=now + timedelta(minutes=RETRY_MINUTES_OTHER),
                                 note=f"خطأ مؤقت من نور: {e.code} — محاولة {attempts}/{MAX_SUBMIT_ATTEMPTS}",
                                 nour_payload=payload, nour_response={"error": e.code, "http": e.http})
    # فشل نهائي → استرداد كامل
    await refund(oid, reason=f"تعذّر الإرسال إلى الشريك ({e.code})", new_status="failed_submit")
    return await repo.update(oid, submit_attempts=attempts, next_retry_at=None, nour_payload=payload,
                             nour_response={"error": e.code, "http": e.http, "final": True})


# ───────────── الاسترداد ─────────────

async def refund(order_id: int, reason: str, new_status: str = "refunded", admin_id: int | None = None,
                 amount: Decimal | None = None) -> dict | None:
    """يعيد المبلغ (كاملاً افتراضياً) للعميل ويغيّر الحالة — معاملة واحدة، ولا يسترد مرتين."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            row = await c.fetchrow("SELECT * FROM orders WHERE id = $1 FOR UPDATE", order_id)
            if not row or row["status"] in ("awaiting_payment", "cancelled"):
                return None
            already = Decimal(row["refunded_usd"] or 0)
            total = Decimal(row["price_usd"])
            amt = money(amount if amount is not None else total - already)
            if amt <= 0 or already + amt > total:
                return None
            await money_svc.credit(row["user_id"], amt, "refund", ref_type="order", ref_id=order_id,
                                   note=f"ORD-{order_id} {reason}"[:200], admin_id=admin_id, conn=c)
            await c.execute(
                "UPDATE orders SET status = $2, refunded_usd = refunded_usd + $3, note = $4, admin_id = COALESCE($5, admin_id), "
                "next_retry_at = NULL, updated_at = now() WHERE id = $1",
                order_id, new_status, amt, reason[:300], admin_id,
            )
    await events.log_event("order_refund", row["user_id"], order_id, amount=str(amt), reason=reason, admin_id=admin_id)
    return await repo.get(order_id)


# ───────────── تطبيق حالة نور ─────────────

async def apply_nour_status(order_id: int, nour_status: str, raw: dict | None = None) -> tuple[dict | None, bool]:
    """يطبّق حالة نور. يعيد (الطلب، هل تغيّرت حالتنا؟). الرفض = استرداد تلقائي كامل."""
    order = await repo.get(order_id)
    if not order:
        return None, False
    local = NOUR_TO_LOCAL.get(nour_status)
    now = datetime.now(timezone.utc)
    if local is None or order["status"] in repo.FINAL_STATUSES:
        await repo.update(order_id, nour_status=nour_status, last_sync_at=now, nour_response=raw)
        return order, False
    if local == order["status"]:
        await repo.update(order_id, nour_status=nour_status, last_sync_at=now)
        return order, False
    if local == "rejected":
        updated = await refund(order_id, reason="رفض الشريك الإعلان — أُعيد المبلغ كاملاً", new_status="rejected")
        await repo.update(order_id, nour_status=nour_status, last_sync_at=now, nour_response=raw)
        return await repo.get(order_id), updated is not None
    fields = dict(status=local, nour_status=nour_status, last_sync_at=now, nour_response=raw)
    if local == "active" and not order.get("started_at"):
        fields["started_at"] = now
    if local == "completed":
        fields["completed_at"] = now
    updated = await repo.update(order_id, **fields)
    await events.log_event("order_status", order["user_id"], order_id, from_=order["status"], to=local)
    return updated, True


async def sync_one(order_id: int) -> tuple[dict | None, bool]:
    """يستعلم نور عن حالة طلب واحد ويطبّقها."""
    order = await repo.get(order_id)
    if not order or not order.get("nour_id") or order["status"] in repo.FINAL_STATUSES:
        return order, False
    try:
        res = await nour.client().get_campaign(order["nour_id"])
    except nour.NourError as e:
        log.warning("sync ORD-%s failed: %s", order_id, e)
        return order, False
    return await apply_nour_status(order_id, res.get("status") or "", res.get("raw"))


# ═══════════════ الطلبات اليدوية (Telegram Ads) — انتقالات الأدمن ═══════════════

# ما يُسمح للأدمن بالانتقال إليه من كل حالة
MANUAL_TRANSITIONS = {
    "submitted":      ("in_progress", "needs_revision", "rejected"),
    "in_progress":    ("active", "needs_revision", "rejected"),
    "needs_revision": ("in_progress", "rejected"),
    "active":         ("completed", "paused"),
    "paused":         ("active", "completed"),
}


async def manual_transition(order_id: int, to: str, admin_id: int, note: str | None = None,
                            results: dict | None = None) -> tuple[dict | None, bool]:
    """ينقل طلباً يدوياً إلى حالة جديدة. الرفض = استرداد كامل تلقائي. يعيد (الطلب، هل تغيّر؟)."""
    order = await repo.get(order_id)
    if not order or order.get("kind") == "tg_post" or to not in MANUAL_TRANSITIONS.get(order["status"], ()):
        return order, False
    now = datetime.now(timezone.utc)
    if to == "rejected":
        await refund(order_id, reason=note or "رفض تيليغرام الإعلان — أُعيد المبلغ كاملاً", new_status="rejected", admin_id=admin_id)
        await repo.update(order_id, nour_status="rejected", last_sync_at=now)
        return await repo.get(order_id), True
    fields: dict = dict(status=to, admin_id=admin_id, last_sync_at=now)
    if to == "needs_revision":
        fields["revision_note"] = (note or "")[:300]
    if to == "active" and not order.get("started_at"):
        fields["started_at"] = now
    if to == "completed":
        fields["completed_at"] = now
        if results:
            fields["results"] = results
    updated = await repo.update(order_id, **fields)
    await events.log_event("order_status", order["user_id"], order_id, from_=order["status"], to=to, admin_id=admin_id)
    return updated, True


async def apply_revision(order_id: int, user_id: int, new_text: str) -> dict | None:
    """العميل أرسل نصاً بديلاً: نحدّث المواصفات ونعيد الطلب إلى قيد المراجعة."""
    order = await repo.get(order_id)
    if not order or order["user_id"] != user_id or order["status"] != "needs_revision":
        return None
    spec = {**order["spec"], "text": new_text, "text_prev": order["spec"].get("text")}
    updated = await repo.update(order_id, status="submitted", spec=spec, revision_note=None, last_sync_at=datetime.now(timezone.utc))
    await events.log_event("order_revised", user_id, order_id)
    return updated
