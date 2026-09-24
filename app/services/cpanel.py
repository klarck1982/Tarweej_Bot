"""منطق Cpanel: التحقق من توقيع تيليغرام، قراءة/حفظ الإعدادات، الإحصائيات، وسجل التغييرات.

الأمان: Mini App ترسل `initData` (سلسلة موقّعة بمفتاح مشتق من توكن البوت). نتحقق من التوقيع (HMAC-SHA256)،
ومن عمر التوقيع (≤ ساعة)، ثم من أن المستخدم ضمن ADMIN_IDS. لا كلمات سر ولا جلسات.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl

from app.config import settings
from app.db import pool as db
from app.db.repo import settings as settings_repo
from app.services import payments as PM
from app.services import pricing as P
from app.services import scheduled as SD

AUTH_MAX_AGE = 3600  # ثانية — بعدها يُطلب فتح جديد من الزر


# ═══════════════════════════ المصادقة ═══════════════════════════

def verify_init_data(init_data: str, token: str | None = None) -> dict | None:
    """يعيد بيانات المستخدم إن كان التوقيع صحيحاً وحديثاً، وإلا None."""
    if not init_data or len(init_data) > 8192:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", (token or settings.bot_token).encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_date <= 0 or time.time() - auth_date > AUTH_MAX_AGE:
        return None
    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None
    return user


def is_admin(user_id: int) -> bool:
    return int(user_id) in settings.admin_ids


def cpanel_url() -> str | None:
    """رابط الـ Mini App — يحتاج https (Render يوفّره)؛ محلياً لا زر."""
    base = settings.public_url.rstrip("/")
    return f"{base}/cpanel" if base.startswith("https://") else None


# ═══════════════════════════ الإعدادات العامة ═══════════════════════════

GENERAL_DEFAULTS: dict[str, Any] = {
    "support_username": "",            # فارغ = من متغير البيئة SUPPORT_USERNAME
    "updates_channel": "",             # رابط قناة العروض (فارغ = من UPDATES_CHANNEL)
    "tg_ads_review_hours": "1 – 24",
    "low_balance_threshold_usd": "50",
    "order_draft_days": 7,
    "min_topup_usd": "5",
    "max_topup_usd": "1000",
    "topup_presets_usd": [5, 10, 20, 50, 100],
    "topup_sla_text": "",
    "maintenance": False,
    "maintenance_msg": "نقوم بتحديث سريع — نعود خلال ساعة. شكراً لصبركم 🙏",
    "disabled_style": "lock",          # lock = زر بشارة قريباً | hide = يختفي
    "design_approve_hours": 48,        # 🎨 الاعتماد التلقائي بعد التسليم إن صمت العميل (ساعات)
    "design_revision_pct": "30",       # 🎨 رسم التعديل بعد المجاني (% من سعر الخدمة)
}


async def general() -> dict:
    """القيم الحالية للإعدادات العامة (من القاعدة أو الافتراضية) — استعلام واحد."""
    stored = await settings_repo.get_many(list(GENERAL_DEFAULTS))
    return {k: (stored[k] if stored.get(k) is not None else dflt) for k, dflt in GENERAL_DEFAULTS.items()}


# نسخة في الذاكرة لما تحتاجه الشاشات في كل ضغطة (بلا استعلام): تُحدَّث عند الإقلاع، بعد كل حفظ، وكل دورة مجدول
_rt: dict[str, Any] = {}


async def refresh_runtime() -> None:
    try:
        _rt.update(await general())
    except Exception:  # noqa: BLE001 — نبقى على آخر نسخة
        pass


def rt(key: str) -> Any:
    """قراءة فورية (متزامنة) لإعداد عام مع الرجوع لمتغيرات البيئة ثم الافتراضي."""
    v = _rt.get(key)
    if v in (None, "") and key == "support_username":
        return settings.support_username
    if v in (None, "") and key == "updates_channel":
        return settings.updates_channel
    return GENERAL_DEFAULTS.get(key) if v is None else v


def maintenance_text() -> str | None:
    """نص التنبيه إن كان وضع الصيانة مفعّلاً، وإلا None."""
    if not rt("maintenance"):
        return None
    return f"🛠️ الطلبات الجديدة متوقفة مؤقتاً\n{rt('maintenance_msg')}"


def _clean_username(v: str) -> str:
    v = (v or "").strip().lstrip("@").split("/")[-1]
    if v and not (5 <= len(v) <= 32 and v.replace("_", "").isalnum()):
        raise ValueError("معرّف الدعم: 5–32 حرفاً إنجليزياً/أرقام/_ بدون @")
    return v


def _clean_url(v: str) -> str:
    v = (v or "").strip()
    if v.startswith("@"):
        v = "https://t.me/" + v[1:]
    elif v.startswith("t.me/"):
        v = "https://" + v
    if v and not v.startswith("https://t.me/"):
        raise ValueError("رابط قناة العروض يجب أن يكون بصيغة https://t.me/اسم_القناة")
    return v[:120]


def validate_general(raw: dict) -> dict:
    out: dict[str, Any] = {}
    out["support_username"] = _clean_username(str(raw.get("support_username", "")))
    out["updates_channel"] = _clean_url(str(raw.get("updates_channel", "")))
    hours = str(raw.get("tg_ads_review_hours", "1 – 24")).strip()[:20] or "1 – 24"
    out["tg_ads_review_hours"] = hours
    try:
        thr = Decimal(str(raw.get("low_balance_threshold_usd", "50")))
        if not 0 <= thr <= 100000:
            raise InvalidOperation
        out["low_balance_threshold_usd"] = str(thr.quantize(Decimal("0.01")))
        mn = Decimal(str(raw.get("min_topup_usd", "5")))
        mx = Decimal(str(raw.get("max_topup_usd", "1000")))
        if not 1 <= mn <= 1000 or not mn <= mx <= 100000:
            raise InvalidOperation
        out["min_topup_usd"] = str(mn.quantize(Decimal("0.01")))
        out["max_topup_usd"] = str(mx.quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        raise ValueError("حدود الشحن/التنبيه: أرقام صحيحة، الأدنى بين 1 و 1000 والأقصى ≥ الأدنى") from None
    presets = []
    for x in raw.get("topup_presets_usd") or []:
        try:
            v = int(Decimal(str(x)))
        except (InvalidOperation, ValueError):
            raise ValueError("الأرقام الجاهزة للشحن: أعداد صحيحة فقط") from None
        if not mn <= v <= mx:
            raise ValueError(f"الرقم الجاهز {v}$ خارج حدود الشحن")
        if v not in presets:
            presets.append(v)
    presets.sort()
    if not 1 <= len(presets) <= 8:
        raise ValueError("الأرقام الجاهزة للشحن: من 1 إلى 8 أرقام")
    out["topup_presets_usd"] = presets
    days = int(raw.get("order_draft_days") or 7)
    if not 1 <= days <= 60:
        raise ValueError("صلاحية المسودة: بين 1 و 60 يوماً")
    out["order_draft_days"] = days
    out["topup_sla_text"] = str(raw.get("topup_sla_text", ""))[:200]
    out["maintenance"] = bool(raw.get("maintenance", False))
    out["maintenance_msg"] = str(raw.get("maintenance_msg") or GENERAL_DEFAULTS["maintenance_msg"])[:300]
    out["disabled_style"] = "hide" if raw.get("disabled_style") == "hide" else "lock"
    try:
        ah_raw = raw.get("design_approve_hours")
        ah = int(Decimal(str(ah_raw if ah_raw not in (None, "") else 48)))
        if not 1 <= ah <= 240:
            raise InvalidOperation
        out["design_approve_hours"] = ah
        pct = Decimal(str(raw.get("design_revision_pct") if raw.get("design_revision_pct") not in (None, "") else "30"))
        if not 0 <= pct <= 100:
            raise InvalidOperation
        out["design_revision_pct"] = str(pct.quantize(Decimal("1")) if pct == pct.to_integral() else pct.quantize(Decimal("0.1")))
    except (InvalidOperation, ValueError):
        raise ValueError("إعدادات التصميم: الاعتماد التلقائي بين 1 و 240 ساعة، ورسم التعديل بين 0 و 100%") from None
    return out


async def save_general(clean: dict, admin_id: int) -> list[str]:
    before = await general()
    changed = []
    for k, v in clean.items():
        if str(before.get(k)) != str(v):
            await settings_repo.set_(k, v)
            changed.append(k)
            await audit(admin_id, "general", k, before.get(k), v)
    await refresh_runtime()
    return changed


# ═══════════════════════════ الأسعار ═══════════════════════════

async def save_pricing(raw: dict, admin_id: int) -> list[str]:
    clean = P.validate(raw)
    try:
        before = P.validate(P.current())   # نطبّع النسخة الحالية بنفس الطريقة حتى لا يُسجَّل "تغيير" شكلي (10 → 10.00)
    except ValueError:   # قيمة قديمة لم تعد تجتاز التحقق (مثل سعر 0) — نسجّل الفرق كما هو
        before = P.current()
    await settings_repo.set_("pricing", clean)
    await P.refresh()
    changed = _diff_keys(before, clean)
    for k, (a, b) in changed.items():
        await audit(admin_id, "pricing", k, a, b)
    return list(changed)


def _diff_keys(a: dict, b: dict, prefix: str = "") -> dict[str, tuple[Any, Any]]:
    out: dict[str, tuple[Any, Any]] = {}
    for k in set(a) | set(b):
        va, vb = a.get(k), b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            out.update(_diff_keys(va, vb, f"{prefix}{k}."))
        elif json.dumps(va, sort_keys=True, default=str) != json.dumps(vb, sort_keys=True, default=str):
            out[f"{prefix}{k}"] = (va, vb)
    return out


# ═══════════════════════════ طرق الدفع ═══════════════════════════

_EDITABLE = ("enabled", "address", "holder")


async def save_payments(raw: dict, admin_id: int) -> list[str]:
    methods = await PM.get_methods()
    # v0.9.2: تحقق كامل قبل أي تعديل/تدقيق — طلب فيه عنوان خاطئ يُرفض كله
    for code, m in methods.items():
        upd = raw.get(code)
        new_addr = str((upd or {}).get("address") or "").strip()[:200] if isinstance(upd, dict) else ""
        if new_addr and new_addr != (m.get("address") or ""):   # نتحقق مما تغيّر فقط
            if not PM.clean_address(m, new_addr):
                raise ValueError(f"{m.get('title', code)}: " + re.sub(r"<[^>]+>", "", PM.address_hint(m)))
    changed = []
    for code, m in methods.items():
        upd = raw.get(code)
        if not isinstance(upd, dict):
            continue
        for f in _EDITABLE:
            if f not in upd:
                continue
            v = bool(upd[f]) if f == "enabled" else str(upd[f] or "").strip()[:200]
            if f == "address" and v:
                v = PM.clean_address(m, v) or v   # سبق التحقق أعلاه — هنا التطبيع فقط
            if m.get(f, "" if f != "enabled" else True) != v:
                await audit(admin_id, "payments", f"{code}.{f}", m.get(f), v)
                m[f] = v
                changed.append(f"{code}.{f}")
    if changed:
        await PM.save_methods(methods)
    return changed


# ═══════════════════════════ الخدمات ═══════════════════════════

SERVICE_NAMES = {"meta": "📢 إعلانات فيسبوك / إنستغرام", "tg_ads": "📣 إعلان Telegram Ads الرسمي",
                 "tg_post": "📝 نشر في قنوات شريكة", "addons": "🎨 تصميم وكتابة", "scheduled": "📅 تصميم يومي مجدول", "ai_reel": "🎬 ريلز سينمائية AI"}
SERVICE_LOCKED = {"tg_post": "يُفتح تلقائياً عند إضافة أول قناة شريكة", "ai_reel": "مقفول — يُفعَّل لاحقاً"}


async def service_locks() -> dict[str, str]:
    """الأقفال الفعلية الآن: القنوات الشريكة تُفتح عندما توجد قناة حيّة واحدة على الأقل."""
    from app.db.repo import partner_channels as PC
    locks = dict(SERVICE_LOCKED)
    if await PC.count_live() > 0:
        locks.pop("tg_post", None)
    return locks


async def save_services(raw: dict, admin_id: int) -> list[str]:
    svc = await settings_repo.services()
    locks = await service_locks()
    changed = []
    for k in svc:
        if k in locks or k not in raw:
            continue
        v = bool(raw[k])
        if svc[k] != v:
            await audit(admin_id, "services", k, svc[k], v)
            svc[k] = v
            changed.append(k)
    if changed:
        await settings_repo.set_("services", svc)
    return changed


# ═══════════════════════════ سجل التغييرات ═══════════════════════════

async def audit(admin_id: int, section: str, key: str, before: Any, after: Any) -> None:
    from app.db.repo import events
    await events.log_event("cpanel_change", admin_id, None, section=section, key=key, before=before, after=after)


async def audit_log(limit: int = 30) -> list[dict]:
    rows = await db.fetch(
        "SELECT user_id, payload, created_at FROM events WHERE type = 'cpanel_change' ORDER BY id DESC LIMIT $1", limit)
    out = []
    for r in rows:
        pl = r["payload"]
        pl = json.loads(pl) if isinstance(pl, str) else (pl or {})
        out.append({"admin_id": r["user_id"], "at": r["created_at"].isoformat(), **pl})
    return out


# ═══════════════════════════ الإحصائيات ═══════════════════════════

_PERIODS = {"today": 1, "7d": 7, "30d": 30, "all": 3650}


async def stats(period: str = "7d") -> dict:
    days = _PERIODS.get(period, 7)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_prev = since - timedelta(days=days)
    users_total = await db.fetchval("SELECT count(*) FROM users") or 0
    users_new = await db.fetchval("SELECT count(*) FROM users WHERE created_at >= $1", since) or 0
    paid = "status NOT IN ('draft','awaiting_payment','cancelled')"
    row = await db.fetchrow(
        f"SELECT count(*) AS n, coalesce(sum(price_usd - refunded_usd),0) AS revenue, "
        f"coalesce(sum(CASE WHEN status IN ('rejected','refunded') THEN 0 ELSE cost_usd END),0) AS cost "
        f"FROM orders WHERE {paid} AND created_at >= $1", since)
    prev = await db.fetchrow(
        f"SELECT coalesce(sum(price_usd - refunded_usd),0) AS revenue FROM orders WHERE {paid} AND created_at >= $1 AND created_at < $2",
        since_prev, since)
    revenue, cost = Decimal(row["revenue"]), Decimal(row["cost"])
    prev_rev = Decimal(prev["revenue"])
    growth = None if prev_rev == 0 else int((revenue - prev_rev) / prev_rev * 100)
    by_status = {r["status"]: r["n"] for r in await db.fetch("SELECT status, count(*) AS n FROM orders GROUP BY status")}
    open_n = await db.fetchval("SELECT count(*) FROM orders WHERE status IN ('paid','submitting','submitted','in_progress','active','paused','needs_revision','failed_submit','delivered')") or 0
    pending_topups = await db.fetchval("SELECT count(*) FROM topups WHERE status='pending'") or 0
    new_orders = by_status.get("submitted", 0) + by_status.get("paid", 0)
    awaiting_text = by_status.get("needs_revision", 0)
    posts_waiting = await db.fetchval("SELECT count(*) FROM orders WHERE kind = 'tg_post' AND status IN ('submitted','in_progress')") or 0
    design_working = await db.fetchval(
        "SELECT count(*) FROM orders WHERE kind = 'design' AND status IN ('submitted','in_progress','needs_revision')") or 0
    design_late = await db.fetchval(
        "SELECT count(*) FROM orders WHERE kind = 'design' AND status IN ('submitted','in_progress','needs_revision') AND due_at < now()") or 0
    liabilities = await db.fetchval("SELECT coalesce(sum(balance_usd),0) FROM users") or 0
    topups_period = await db.fetchval("SELECT coalesce(sum(amount_usd),0) FROM topups WHERE status='approved' AND decided_at >= $1", since) or 0
    # الإيراد اليومي لآخر 7 أيام (للرسم)
    daily = await db.fetch(
        f"SELECT date_trunc('day', created_at) AS d, coalesce(sum(price_usd - refunded_usd),0) AS v FROM orders "
        f"WHERE {paid} AND created_at >= $1 GROUP BY 1 ORDER BY 1", datetime.now(timezone.utc) - timedelta(days=6))
    dmap = {r["d"].date().isoformat(): float(r["v"]) for r in daily}
    chart = []
    for i in range(6, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
        chart.append({"d": d, "v": dmap.get(d, 0.0)})
    top = await db.fetch(
        f"SELECT kind, CASE WHEN kind = 'design' THEN coalesce(spec->>'title','') ELSE coalesce(spec->>'pkg','') END AS pkg, "
        f"count(*) AS n FROM orders WHERE {paid} AND created_at >= $1 "
        f"GROUP BY 1,2 ORDER BY n DESC LIMIT 5", since)
    top_services = [{"label": _service_label(r["kind"], r["pkg"]), "n": r["n"]} for r in top]
    nour_balance = None
    try:
        from app.services import nour
        acc = await nour.client().get_account()
        nour_balance = float(acc["balance"]) if acc.get("balance") is not None else None
    except Exception:  # noqa: BLE001
        nour_balance = None
    return {
        "period": period, "users_total": users_total, "users_new": users_new,
        "orders": row["n"], "orders_open": open_n, "revenue": float(revenue), "cost": float(cost),
        "profit": float(revenue - cost), "margin_pct": (int((revenue - cost) / revenue * 100) if revenue else 0),
        "growth_pct": growth, "by_status": by_status, "pending_topups": pending_topups, "new_orders": new_orders,
        "awaiting_text": awaiting_text, "posts_waiting": posts_waiting,
        "design_working": design_working, "design_late": design_late, "liabilities": float(liabilities), "topups_period": float(topups_period),
        "chart": chart, "top_services": top_services, "nour_balance": nour_balance,
        "nour_dry": _nour_dry(),
    }


def _nour_dry() -> bool:
    from app.services import nour
    return nour.is_dry_run()


def _service_label(kind: str, pkg: str) -> str:
    if kind == "tg_ads":
        return "📣 Telegram Ads"
    if kind == "meta_campaign":
        p = P.META_BY_CODE.get(pkg)
        if p:
            return f"📢 Meta — {p.emoji} {p.title}"
        return "📢 Meta — 📦 انطلاقة متجر" if pkg == "bundle" else "📢 Meta — 🛠️ مخصص"
    if kind == "design":
        return "🎨 تصميم — " + (pkg or "خدمة")
    names = {"copy": "✍️ نص إعلاني", "reel": "🎬 ريل", "montage": "🎞️ مونتاج", "tg_post": "📝 قنوات شريكة"}
    return names.get(kind, kind)


# ═══════════════════════════ القنوات الشريكة ═══════════════════════════

async def partner_channels_view() -> dict:
    from app.db.repo import partner_channels as PC
    from app.services import partner_posts as PP
    items = [PP.channel_view(c) for c in await PC.list_all()]
    return {"items": items, "categories": {k: f"{e} {n}" for k, (e, n) in PC.CATEGORIES.items()},
            "live": sum(1 for c in items if c["enabled"] and not c["archived"]), "month": await PC.month_stats(),
            "mult": str(P.TG_POST_MULT), "pin_extra": str(P.TG_POST_PIN_EXTRA)}


async def save_partner_channel(raw: dict, admin_id: int) -> dict:
    """إنشاء/تعديل قناة من Cpanel. يعيد القناة المحفوظة (يرمي ValueError عند خطأ إدخال)."""
    from app.db.repo import partner_channels as PC
    from app.services import partner_posts as PP
    data = PP.validate_channel(raw)
    cid = raw.get("id")
    if cid:
        before = await PC.get(int(cid))
        if not before:
            raise ValueError("القناة غير موجودة")
        ch = await PC.update(int(cid), data)
        for k, v in data.items():
            if str(before.get(k)) != str(v):
                await audit(admin_id, "partner", f"{ch['title']}.{k}", before.get(k), v)
    else:
        ch = await PC.create(data)
        await audit(admin_id, "partner", "إضافة قناة", None, f"{ch['title']} · {ch['price_24h']}$")
    return PP.channel_view(ch)


async def toggle_partner_channel(channel_id: int, enabled: bool, admin_id: int) -> dict | None:
    from app.db.repo import partner_channels as PC
    from app.services import partner_posts as PP
    ch = await PC.get(channel_id)
    if not ch:
        return None
    if ch["enabled"] != enabled:
        ch = await PC.update(channel_id, {"enabled": enabled})
        await audit(admin_id, "partner", f"{ch['title']}.enabled", not enabled, enabled)
    return PP.channel_view(ch)


async def delete_partner_channel(channel_id: int, admin_id: int) -> str:
    from app.db.repo import partner_channels as PC
    ch = await PC.get(channel_id)
    if not ch:
        return "missing"
    res = await PC.delete_or_archive(channel_id)
    await audit(admin_id, "partner", f"{ch['title']}.{res}", True, False)
    return res


async def channels_view() -> list[dict]:
    from app.services import channels as CH
    cfg = await CH.all_cfg()
    out = []
    for kind, (emoji, name) in CH.KINDS.items():
        ch = cfg.get(kind) or {}
        out.append({"kind": kind, "emoji": emoji, "name": name, "bound": bool(ch), "title": ch.get("title", ""), "id": ch.get("id")})
    return out


async def snapshot() -> dict:
    """كل ما تحتاجه الواجهة في طلب واحد."""
    from app import STEP, VERSION
    return {
        "version": VERSION, "step": STEP, "mode": settings.mode, "bot_name": settings.bot_name,
        "admins": list(settings.admin_ids),
        "pricing": P.current(), "pricing_defaults": P.DEFAULTS,
        "general": await general(), "general_env": {"support_username": settings.support_username, "updates_channel": settings.updates_channel},
        "payments": await PM.get_methods(), "payment_order": PM.METHOD_ORDER, "syp_rate": str(await PM.syp_rate()),
        "services": await settings_repo.services(), "service_names": SERVICE_NAMES, "service_locked": await service_locks(),
        "scheduled": await SD.snapshot(),
        "channels": await channels_view(), "partner": await partner_channels_view(), "audit": await audit_log(30),
        "nour": await _nour_view(), "reset": await _reset_preview(),
    }


async def _nour_view() -> dict:
    from app.services import nour_health as NH
    try:
        return await NH.status_view()
    except Exception as e:  # noqa: BLE001
        return {"dry": _nour_dry(), "error": str(e)[:120]}


async def _reset_preview() -> dict:
    from app.services import launch_reset as LR
    try:
        return await LR.preview()
    except Exception as e:  # noqa: BLE001
        return {"allowed": False, "blockers": [str(e)[:120]], "counts": {}}
