"""صحة اتصال Nour Ads + مراقبة الرصيد + المطابقة اليومية (v0.7.0 — الخطوة 4).

    check()            → اختبار الاتصال الآن (يستخدمه زر «🔌 اختبار الاتصال» في Cpanel وفحص الإقلاع)
    watch_balance()    → يفحص الرصيد كل ساعة: تنبيه عند النزول تحت الحد، تذكير يومي، «عاد الرصيد» عند الشحن
    daily_report()     → تقرير المطابقة الساعة 09:00 (رصيد نور مقابل التزامات العملاء + الطلبات العالقة + فروق التكلفة)
    startup_check()    → عند الإقلاع: رسالة «🟢 متصل — الرصيد X$» أو «🔴 التوكن مرفوض»

كل الحالات تُحفظ في settings (nour_health) حتى لا تتكرر التنبيهات بعد إعادة التشغيل.
لا شيء هنا يوقف البوت: أي فشل يُسجَّل ويُبلَّغ فقط.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.config import settings
from app.db import pool as db
from app.db.repo import settings as settings_repo
from app.services import nour, pricing as P

log = logging.getLogger("nour_health")

_KEY = "nour_health"          # {last_ok_at, last_error, balance, account_name, low_alert_at, low_reminded_at, report_date}
REPORT_HOUR = 9               # 09:00 بتوقيت المشروع
BALANCE_CHECK_MINUTES = 60
STUCK_PENDING_HOURS = 24      # submitted (بانتظار نور) أطول من ذلك = عالق
STUCK_PROGRESS_HOURS = 48     # in_progress أطول من ذلك = عالق


async def _state() -> dict:
    return dict(await settings_repo.get(_KEY, {}) or {})


async def _save(st: dict) -> None:
    await settings_repo.set_(_KEY, st)


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def threshold() -> Decimal:
    from app.services import cpanel
    try:
        return Decimal(str(cpanel.rt("low_balance_threshold_usd") or "50"))
    except Exception:  # noqa: BLE001
        return Decimal("50")


# ═══════════════════════════ اختبار الاتصال ═══════════════════════════

async def check() -> dict:
    """يستعلم /account ويعيد {ok, dry, balance, account, error, code, checked_at} ويحفظ آخر نتيجة."""
    client = nour.client()
    now = datetime.now(timezone.utc)
    st = await _state()
    out = {"ok": False, "dry": client.dry_run, "balance": None, "account": None, "error": None, "code": None,
           "token_set": bool(settings.nour_ads_token), "checked_at": now.isoformat()}
    try:
        acc = await client.get_account()
        raw = acc.get("raw") or {}
        client_info = raw.get("client") or {}
        out.update(ok=True, balance=float(acc["balance"]) if acc.get("balance") is not None else None,
                   account=client_info.get("name") or ("محاكاة 🧪" if client.dry_run else None))
        st.update(last_ok_at=now.isoformat(), last_error=None, balance=out["balance"], account_name=out["account"])
    except nour.NourError as e:
        msg = {"unauthorized": "التوكن مرفوض (401) — تأكد من NOUR_ADS_TOKEN في Render",
               "forbidden": "الحساب ممنوع (403) — راسل دعم Nour Ads",
               "network": "تعذّر الوصول إلى nour-ads.com — الشبكة أو الموقع متوقف مؤقتاً",
               "rate_limited": "طلبات كثيرة (429) — أعد المحاولة بعد دقيقة"}.get(e.code, f"{e.code}: {e.message[:120]}")
        out.update(error=msg, code=e.code)
        st.update(last_error=msg, last_error_at=now.isoformat())
    except Exception as e:  # noqa: BLE001
        out.update(error=f"خطأ غير متوقع: {str(e)[:120]}", code="unexpected")
        st.update(last_error=out["error"], last_error_at=now.isoformat())
    await _save(st)
    return out


async def status_view() -> dict:
    """ما تعرضه بطاقة Nour في Cpanel بلا استعلام شبكة (آخر نتيجة محفوظة)."""
    st = await _state()
    dry = nour.is_dry_run()
    return {"dry": dry, "token_set": bool(settings.nour_ads_token), "last_ok_at": st.get("last_ok_at"),
            "last_error": st.get("last_error"), "last_error_at": st.get("last_error_at"), "balance": st.get("balance"),
            "account": st.get("account_name"), "threshold": str(threshold()), "low_alert_at": st.get("low_alert_at"),
            "report_date": st.get("report_date")}


# ═══════════════════════════ مراقبة الرصيد ═══════════════════════════

async def liabilities() -> dict:
    """ما يجب أن يغطيه رصيد نور: تكلفة طلبات Meta المدفوعة التي لم تُرسل بعد + أرصدة العملاء الجاهزة للإنفاق."""
    pending_cost = await db.fetchval(
        "SELECT coalesce(sum(cost_usd),0) FROM orders WHERE kind='meta_campaign' AND status='paid'") or 0
    balances = await db.fetchval("SELECT coalesce(sum(balance_usd),0) FROM users") or 0
    # الرصيد يُنفق بسعر العميل؛ ما يقابله عند نور = الرصيد ÷ مضاعف السعر × (1 + عمولة نور)
    try:
        mult = Decimal(str(P.CLIENT_MULT))
        cost_mult = Decimal(str(P.NOUR_MULT))
    except Exception:  # noqa: BLE001
        mult, cost_mult = Decimal("1.30"), Decimal("1.10")
    exposure = (Decimal(balances) / mult * cost_mult).quantize(Decimal("0.01"))
    return {"pending_cost": Decimal(pending_cost), "client_balances": Decimal(balances), "exposure": exposure}


async def watch_balance(bot: Bot, force: bool = False) -> None:
    """يُستدعى من المجدول كل دورة؛ يفحص فعلياً كل ساعة (أو فوراً مع force)."""
    if nour.is_dry_run():
        return
    st = await _state()
    now = datetime.now(timezone.utc)
    last = st.get("balance_checked_at")
    if not force and last and (now - datetime.fromisoformat(last)).total_seconds() < BALANCE_CHECK_MINUTES * 60:
        return
    res = await check()
    st = await _state()
    st["balance_checked_at"] = now.isoformat()
    if not res["ok"]:
        # فشل الاتصال: تنبيه واحد كل 6 ساعات فقط
        last_fail = st.get("fail_alert_at")
        if not last_fail or (now - datetime.fromisoformat(last_fail)) > timedelta(hours=6):
            st["fail_alert_at"] = now.isoformat()
            await _alert(bot, f"🔴 <b>Nour Ads غير متاح:</b> {esc(res['error'])}\nطلبات Meta الجديدة تنتظر بأمان (مال العميل محفوظ) ويُعاد إرسالها تلقائياً.")
        await _save(st)
        return
    st.pop("fail_alert_at", None)
    bal = Decimal(str(res["balance"] if res["balance"] is not None else 0))
    thr = threshold()
    li = await liabilities()
    if bal < thr:
        low_at = st.get("low_alert_at")
        reminded = st.get("low_reminded_at")
        first = not low_at
        due_reminder = low_at and (not reminded or (now - datetime.fromisoformat(reminded)) > timedelta(hours=24))
        if first or due_reminder:
            head = "⚠️ <b>رصيد Nour Ads منخفض</b>" if first else "⏰ <b>تذكير: رصيد Nour Ads ما زال منخفضاً</b>"
            await _alert(bot, f"{head}\n💼 الرصيد: <b>{P.fmt(bal)}</b> (الحد {P.fmt(thr)})\n"
                              f"📦 طلبات بانتظار الإرسال: تكلفتها <b>{P.fmt(li['pending_cost'])}</b>\n"
                              f"👥 أرصدة العملاء: {P.fmt(li['client_balances'])} (تحتاج ≈ {P.fmt(li['exposure'])} عند نور لو أُنفقت كلها)\n"
                              f"اشحن من لوحة nour-ads.com — الطلبات المعلّقة تُرسل تلقائياً بعد الشحن.")
            if first:
                st["low_alert_at"] = now.isoformat()
            st["low_reminded_at"] = now.isoformat()
    else:
        if st.get("low_alert_at"):
            await _alert(bot, f"✅ <b>عاد رصيد Nour Ads:</b> {P.fmt(bal)} — الطلبات المعلّقة ستُرسل خلال دقائق.")
            # نُسرّع إعادة الإرسال: الطلبات المنتظرة تصبح مستحقة الآن
            await db.execute("UPDATE orders SET next_retry_at = now() WHERE kind='meta_campaign' AND status='paid'")
        st.pop("low_alert_at", None)
        st.pop("low_reminded_at", None)
    await _save(st)


# ═══════════════════════════ المطابقة اليومية ═══════════════════════════

async def daily_report(bot: Bot, force: bool = False) -> str | None:
    """يُرسل مرة يومياً بعد الساعة 09:00 (بتوقيت المشروع). يعيد النص المُرسل أو None."""
    try:
        local_now = datetime.now(ZoneInfo(settings.tz))
    except Exception:  # noqa: BLE001
        local_now = datetime.now(timezone.utc)
    st = await _state()
    today = local_now.date().isoformat()
    if not force and (local_now.hour < REPORT_HOUR or st.get("report_date") == today):
        return None
    text = await build_report()
    await _alert(bot, text)
    st = await _state()
    st["report_date"] = today
    await _save(st)
    return text


async def build_report() -> str:
    dry = nour.is_dry_run()
    res = await check() if not dry else {"ok": True, "balance": None}
    li = await liabilities()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    day = await db.fetchrow(
        "SELECT count(*) AS n, coalesce(sum(price_usd - refunded_usd),0) AS rev, "
        "coalesce(sum(CASE WHEN status IN ('rejected','refunded','failed_submit') THEN 0 ELSE cost_usd END),0) AS cost "
        "FROM orders WHERE status NOT IN ('draft','awaiting_payment','cancelled') AND paid_at >= $1", since)
    topups = await db.fetchval("SELECT coalesce(sum(amount_usd),0) FROM topups WHERE status='approved' AND decided_at >= $1", since) or 0
    stuck_pending = await db.fetch(
        "SELECT id FROM orders WHERE kind='meta_campaign' AND status='submitted' AND submitted_at < now() - ($1 || ' hours')::interval ORDER BY id",
        str(STUCK_PENDING_HOURS))
    stuck_progress = await db.fetch(
        "SELECT id FROM orders WHERE kind='meta_campaign' AND status='in_progress' AND updated_at < now() - ($1 || ' hours')::interval ORDER BY id",
        str(STUCK_PROGRESS_HOURS))
    waiting = await db.fetch("SELECT id, note FROM orders WHERE kind='meta_campaign' AND status='paid' ORDER BY id")
    diffs = await db.fetch(
        "SELECT id, cost_usd, charged_usd FROM orders WHERE kind='meta_campaign' AND charged_usd IS NOT NULL "
        "AND abs(charged_usd - cost_usd) > 0.05 AND paid_at >= $1 ORDER BY id", since)
    open_n = await db.fetchval("SELECT count(*) FROM orders WHERE kind='meta_campaign' AND status IN ('submitted','in_progress','active','paused')") or 0

    lines = ["🧾 <b>مطابقة Nour Ads اليومية</b>" + (" 🧪" if dry else "")]
    if dry:
        lines.append("💼 الرصيد: وضع المحاكاة — لا اتصال حقيقي")
    elif res["ok"]:
        bal = Decimal(str(res["balance"] or 0))
        flag = " ⚠️" if bal < threshold() else ""
        lines.append(f"💼 رصيد نور: <b>{P.fmt(bal)}</b>{flag}")
        need = li["pending_cost"] + li["exposure"]
        lines.append(f"🛡️ التغطية: يلزم ≈ {P.fmt(need)} (طلبات منتظرة {P.fmt(li['pending_cost'])} + أرصدة العملاء ≈ {P.fmt(li['exposure'])})"
                     + (" — <b>مكشوف</b> ❗" if bal < need else " — مغطّى ✅"))
    else:
        lines.append(f"🔴 تعذّر الاتصال: {esc(res['error'])}")
    lines.append(f"📅 آخر 24 ساعة: {day['n']} طلب · إيراد {P.fmt(day['rev'])} · تكلفة {P.fmt(day['cost'])} · ربح <b>{P.fmt(Decimal(day['rev']) - Decimal(day['cost']))}</b> · شحنات معتمدة {P.fmt(topups)}")
    lines.append(f"📦 حملات مفتوحة عند نور: {open_n}")
    if waiting:
        lines.append("⏳ <b>بانتظار الإرسال لنور:</b> " + " · ".join(f"#ORD-{r['id']}" for r in waiting[:10]) + (f" (+{len(waiting) - 10})" if len(waiting) > 10 else ""))
    if stuck_pending:
        lines.append(f"🟡 <b>عالقة بانتظار نور &gt; {STUCK_PENDING_HOURS} س:</b> " + " · ".join(f"#ORD-{r['id']}" for r in stuck_pending[:10]) + " — راسل @Nour_Ads")
    if stuck_progress:
        lines.append(f"🔵 <b>قيد التنفيذ &gt; {STUCK_PROGRESS_HOURS} س:</b> " + " · ".join(f"#ORD-{r['id']}" for r in stuck_progress[:10]))
    if diffs:
        lines.append("💱 <b>فروق تكلفة:</b> " + " · ".join(f"#ORD-{r['id']} توقعنا {P.fmt(r['cost_usd'])} خصم نور {P.fmt(r['charged_usd'])}" for r in diffs[:5]))
    if not (waiting or stuck_pending or stuck_progress or diffs):
        lines.append("✅ لا شيء عالق ولا فروق — كل شيء متطابق.")
    return "\n".join(lines)


# ═══════════════════════════ الإقلاع ═══════════════════════════

async def startup_check(bot: Bot) -> None:
    """عند التشغيل: في الوضع الحقيقي نختبر التوكن ونبلّغ؛ في المحاكاة رسالة تذكير قصيرة."""
    if nour.is_dry_run():
        if settings.nour_ads_token:
            res = await check()
            msg = (f"🧪 <b>Nour Ads: وضع المحاكاة</b> — التوكن موجود ويعمل ✅ (حساب: {esc(res['account'] or '—')}، الرصيد {P.fmt(res['balance'] or 0)}).\n"
                   f"للانتقال إلى الحقيقي: اضبط <code>NOUR_DRY_RUN=0</code> في Render."
                   if res["ok"] else f"🧪 <b>Nour Ads: وضع المحاكاة</b> — التوكن موجود لكنه <b>مرفوض</b>: {esc(res['error'])}")
            await _alert(bot, msg)
        return
    res = await check()
    if res["ok"]:
        await _alert(bot, f"🟢 <b>Nour Ads متصل</b> — الحساب: <b>{esc(res['account'] or '—')}</b> · الرصيد: <b>{P.fmt(res['balance'] or 0)}</b>\n"
                          f"طلبات Meta تُرسل الآن فعلياً. حد التنبيه: {P.fmt(threshold())}.")
        await watch_balance(bot, force=True)
    else:
        await _alert(bot, f"🔴 <b>Nour Ads: فشل الاتصال عند الإقلاع</b> — {esc(res['error'])}\n"
                          f"البوت يعمل، وطلبات Meta المدفوعة تنتظر (لن يُخصم من العميل مرتين) حتى يُصلَح التوكن.")


async def _alert(bot: Bot, text: str) -> None:
    try:
        from app.services import channels
        await channels.alert(bot, text)
    except Exception as e:  # noqa: BLE001
        log.warning("nour alert failed: %s", e)
