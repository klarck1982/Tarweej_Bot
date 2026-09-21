"""باقات التصميم المجدولة: تصميم + نص كتابي يومياً لكل مشترك."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.db import pool as db
from app.db.repo import scheduled as repo
from app.db.repo import settings as settings_repo
from app.services import money
from app.services.pricing import money as money_amount

PACKAGES_KEY = "scheduled_design_packages"
DEFAULT_TIMEZONE = "Asia/Damascus"


def _defaults() -> list[dict]:
    return []


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
        days = int(raw.get("duration_days"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("السعر وعدد التصاميم والأيام يجب أن تكون أرقاماً صحيحة") from None
    if price <= 0 or price > 100000:
        raise ValueError("السعر يجب أن يكون أكبر من صفر")
    if not 1 <= total <= 365:
        raise ValueError("عدد التسليمات بين 1 و365")
    if not 1 <= days <= 365 or days < total:
        raise ValueError("مدة الباقة يجب أن تكون بين 1 و365 وألا تقل عن عدد التسليمات")
    send_time = str(raw.get("send_time") or "20:00").strip()
    try:
        hour, minute = (int(x) for x in send_time.split(":", 1))
        time(hour, minute)
    except (ValueError, TypeError):
        raise ValueError("وقت الإرسال يجب أن يكون بصيغة HH:MM") from None
    return {
        "code": code,
        "title": title,
        "description": desc,
        "price_usd": f"{price:.2f}",
        "total_items": total,
        "duration_days": days,
        "send_time": f"{hour:02d}:{minute:02d}",
        "timezone": str(raw.get("timezone") or DEFAULT_TIMEZONE)[:64],
        "include_copy": bool(raw.get("include_copy", True)),
        "enabled": bool(raw.get("enabled", True)),
    }


async def save_package(raw: dict, admin_id: int | None = None) -> tuple[dict, bool]:
    clean = validate_package(raw)
    rows = await packages()
    existed = any(p.get("code") == clean["code"] for p in rows)
    if not existed:
        rows.append(clean)
    else:
        rows = [clean if p.get("code") == clean["code"] else p for p in rows]
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


def _package_spec(package: dict) -> dict:
    return {
        "scheduled_subscription": True,
        "package_code": package["code"],
        "package_title": package["title"],
        "total_items": int(package["total_items"]),
        "duration_days": int(package["duration_days"]),
        "send_time": package["send_time"],
        "timezone": package.get("timezone") or DEFAULT_TIMEZONE,
        "include_copy": bool(package.get("include_copy", True)),
    }


async def purchase(user_id: int, code: str) -> dict:
    """شراء الباقة من الرصيد؛ تُنشأ بانتظار إدخال المحتوى من Cpanel."""
    package = await get_package(code, enabled_only=True)
    if not package:
        raise ValueError("الباقة غير متاحة حالياً")
    price = money_amount(Decimal(str(package["price_usd"])))
    spec = _package_spec(package)
    async with db.pool().acquire() as c:
        async with c.transaction():
            balance = Decimal(await c.fetchval("SELECT balance_usd FROM users WHERE tg_id=$1 FOR UPDATE", user_id) or 0)
            if balance < price:
                raise money.InsufficientBalance(balance, price)
            order = await c.fetchrow(
                """
                INSERT INTO orders (user_id, kind, status, spec, price_usd, cost_usd, paid_at, note)
                VALUES ($1, 'design', 'active', $2::jsonb, $3, 0, now(), $4)
                RETURNING id
                """,
                user_id, __import__("json").dumps(spec, ensure_ascii=False), price,
                f"اشتراك تصميم مجدول — {package['title']}",
            )
            sub = await c.fetchrow(
                """
                INSERT INTO scheduled_subscriptions
                    (user_id, order_id, package_code, package_title, price_usd, total_items, duration_days, send_time, timezone)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8::time,$9)
                RETURNING id
                """,
                user_id, order["id"], package["code"], package["title"], price,
                int(package["total_items"]), int(package["duration_days"]), package["send_time"],
                package.get("timezone") or DEFAULT_TIMEZONE,
            )
            await c.execute("UPDATE orders SET scheduled_subscription_id=$2 WHERE id=$1", order["id"], sub["id"])
            await c.execute(
                "INSERT INTO ledger (user_id,type,amount_usd,ref_type,ref_id,note) VALUES ($1,'order_charge',$2,'subscription',$3,$4)",
                user_id, -price, sub["id"], f"SUB-{sub['id']} {package['title']}",
            )
            await c.execute("UPDATE users SET balance_usd=balance_usd-$2 WHERE tg_id=$1", user_id, price)
    result = await repo.get_subscription(int(sub["id"]))
    if not result:
        raise RuntimeError("تعذر إنشاء الاشتراك")
    return result


def _local_start(start_date: str, send_time: str, tz_name: str) -> tuple[datetime, ZoneInfo]:
    try:
        tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    try:
        y, m, d = (int(x) for x in str(start_date).split("-"))
        hh, mm = (int(x) for x in str(send_time).split(":", 1))
        local = datetime(y, m, d, hh, mm, tzinfo=tz)
    except (ValueError, TypeError):
        raise ValueError("تاريخ أو وقت البداية غير صالح") from None
    return local, tz


def schedule_times(total: int, start_date: str, send_time: str, tz_name: str) -> tuple[datetime, list[datetime]]:
    local, _ = _local_start(start_date, send_time, tz_name)
    times = [(local + timedelta(days=i)).astimezone(timezone.utc) for i in range(total)]
    return local.astimezone(timezone.utc), times


async def activate(subscription_id: int, start_date: str, send_time: str | None = None) -> dict:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        raise ValueError("المشترك غير موجود")
    package = await get_package(sub["package_code"], enabled_only=False) or {}
    use_time = send_time or str(sub.get("send_time") or package.get("send_time") or "20:00")
    rows = await repo.items(subscription_id)
    needed = int(sub["total_items"])
    by_seq = {int(r["seq"]): r for r in rows}
    missing = [str(n) for n in range(1, needed + 1) if n not in by_seq or not by_seq[n].get("file_id")]
    if missing:
        raise ValueError("التصاميم الناقصة: " + ", ".join(missing[:20]))
    if bool((package or {}).get("include_copy", True)):
        missing_text = [str(n) for n in range(1, needed + 1) if not (by_seq[n].get("copy_text") or "").strip()]
        if missing_text:
            raise ValueError("النصوص الناقصة: " + ", ".join(missing_text[:20]))
    start_date = str(start_date or "")
    start_utc, times = schedule_times(needed, start_date, use_time, sub.get("timezone") or DEFAULT_TIMEZONE)
    return await repo.activate(subscription_id, start_utc, use_time, times)


async def reschedule(subscription_id: int, start_date: str, send_time: str) -> dict:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        raise ValueError("المشترك غير موجود")
    _, times = schedule_times(int(sub["total_items"]), start_date, send_time, sub.get("timezone") or DEFAULT_TIMEZONE)
    return await repo.update_schedule(subscription_id, send_time, times[0], times)


async def add_content(subscription_id: int, seq: int, file_kind: str, file_id: str, copy_text: str) -> dict:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        raise ValueError("المشترك غير موجود")
    seq = int(seq)
    if not 1 <= seq <= int(sub["total_items"]):
        raise ValueError(f"رقم اليوم يجب أن يكون بين 1 و{sub['total_items']}")
    if file_kind not in ("photo", "document", "video") or not file_id:
        raise ValueError("ملف التصميم غير صالح")
    return await repo.upsert_item(subscription_id, seq, file_kind, file_id, copy_text)


async def cancel(subscription_id: int, admin_id: int | None = None) -> dict | None:
    """إلغاء الباقة ورد قيمة الأيام غير المنفذة إلى رصيد العميل."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            sub = await c.fetchrow("SELECT * FROM scheduled_subscriptions WHERE id=$1 FOR UPDATE", subscription_id)
            if not sub or sub["status"] in ("cancelled", "refunded", "completed"):
                return dict(sub) if sub else None
            sent = int(await c.fetchval(
                "SELECT count(*) FROM scheduled_subscription_items WHERE subscription_id=$1 AND status='sent'",
                subscription_id,
            ) or 0)
            total = int(sub["total_items"])
            remaining = max(0, total - sent)
            price = Decimal(sub["price_usd"] or 0)
            refund = money_amount((price * Decimal(remaining) / Decimal(total)) if total else price)
            if refund > 0:
                await c.execute(
                    "INSERT INTO ledger (user_id,type,amount_usd,ref_type,ref_id,note,admin_id) VALUES ($1,'refund',$2,'subscription',$3,$4,$5)",
                    sub["user_id"], refund, subscription_id, f"استرداد اشتراك SUB-{subscription_id} — {remaining}/{total} غير منفذ", admin_id,
                )
                await c.execute("UPDATE users SET balance_usd=balance_usd+$2 WHERE tg_id=$1", sub["user_id"], refund)
            await c.execute(
                "UPDATE scheduled_subscriptions SET status='refunded', last_error=$2, updated_at=now() WHERE id=$1",
                subscription_id, f"استُرد {refund}$ — المنفذ {sent}/{total}",
            )
            if sub["order_id"]:
                await c.execute(
                    "UPDATE orders SET status='refunded', refunded_usd=$2, note=$3, updated_at=now() WHERE id=$1",
                    sub["order_id"], refund, f"استرداد اشتراك مجدول SUB-{subscription_id}",
                )
    return await repo.get_subscription(subscription_id)


async def pause(subscription_id: int) -> dict | None:
    return await repo.set_status(subscription_id, "paused")


async def resume(subscription_id: int) -> dict | None:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        return None
    if sub.get("status") != "paused":
        return sub
    return await repo.set_status(subscription_id, "scheduled")


async def snapshot() -> dict:
    return {"packages": await packages(include_disabled=True), "count": await repo.count_subscriptions()}


async def detail(subscription_id: int) -> dict | None:
    sub = await repo.get_subscription(subscription_id)
    if not sub:
        return None
    sub["items"] = await repo.items(subscription_id)
    return sub


async def client_subscriptions(user_id: int, limit: int = 10) -> list[dict]:
    rows = await repo.list_subscriptions(str(user_id), limit=limit, offset=0)
    return [r for r in rows if int(r.get("user_id") or 0) == int(user_id)]
