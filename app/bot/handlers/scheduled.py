"""🎨 باقات التصميم المجدولة — شراء باقة تصميم + نص يومي من الرصيد."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

log = logging.getLogger("scheduled")

from app.bot import keyboards as K
from app.bot import texts as T
from app.db.repo import scheduled as SR
from app.services import notify
from app.services import scheduled as SD
from app.services.money import InsufficientBalance
from app.services.pricing import fmt

router = Router(name="scheduled")


def _esc(value) -> str:
    return T.esc(str(value or ""))


@router.callback_query(F.data == "sub:list")
async def cb_list(cb: CallbackQuery) -> None:
    packages = await SD.packages(include_disabled=False)
    if not packages:
        await cb.message.edit_text(
            "📅 <b>باقات التصميم اليومي</b>\n\nلا توجد باقات متاحة حالياً. تواصل مع الدعم إذا كنت تريد الاشتراك.",
            reply_markup=K.home_only(),
        )
        await cb.answer()
        return
    lines = ["📅 <b>باقات التصميم اليومي</b>", "", "كل يوم يصلك تصميم مع النص الكتابي الخاص به في الموعد المحدد.", ""]
    for p in packages:
        lines.append(f"🎨 <b>{_esc(p['title'])}</b> — <b>{fmt(p['price_usd'])}</b> · {int(p['total_items'])} تصميم")
    await cb.message.edit_text("\n".join(lines), reply_markup=K.scheduled_package_list(packages))
    await cb.answer()


@router.callback_query(F.data.startswith("sub:pkg:"))
async def cb_package(cb: CallbackQuery) -> None:
    code = cb.data.split(":", 2)[2]
    package = await SD.get_package(code, enabled_only=True)
    if not package:
        await cb.answer("هذه الباقة لم تعد متاحة.", show_alert=True)
        return
    text = (
        f"🎨 <b>{_esc(package['title'])}</b>\n\n"
        f"{_esc(package.get('description') or 'تصميم + نص كتابي كل يوم')}\n\n"
        f"📦 العدد: <b>{int(package['total_items'])}</b> تصميم ونص\n"
        f"📅 المدة: <b>{int(package['duration_days'])}</b> يوم\n"
        f"⏰ الموعد الافتراضي: <b>{_esc(package.get('send_time') or '20:00')}</b>\n"
        f"💵 السعر: <b>{fmt(package['price_usd'])}</b>\n\n"
        "بعد الدفع يجهّز فريقنا التصاميم الخاصة بك ويبدأ الإرسال حسب الموعد المتفق عليه."
    )
    await cb.message.edit_text(text, reply_markup=K.scheduled_package_detail(package))
    await cb.answer()


@router.callback_query(F.data.startswith("sub:buy:"))
async def cb_buy(cb: CallbackQuery) -> None:
    code = cb.data.split(":", 2)[2]
    package = await SD.get_package(code, enabled_only=True)
    if not package:
        await cb.answer("هذه الباقة لم تعد متاحة.", show_alert=True)
        return
    balance = await __import__("app.db.repo.users", fromlist=["get_balance"]).get_balance(cb.from_user.id)
    price = fmt(package["price_usd"])
    text = (
        f"🧾 <b>مراجعة الاشتراك</b>\n\n"
        f"الباقة: <b>{_esc(package['title'])}</b>\n"
        f"التسليمات: <b>{int(package['total_items'])}</b> تصميم + نص\n"
        f"السعر: <b>{price}</b>\n"
        f"رصيدك الحالي: <b>{fmt(balance)}</b>\n\n"
        "لن يُخصم المبلغ إلا بعد ضغط زر التأكيد."
    )
    await cb.message.edit_text(text, reply_markup=K.scheduled_purchase_confirm(code))
    await cb.answer()


@router.callback_query(F.data.startswith("sub:confirm:"))
async def cb_confirm(cb: CallbackQuery) -> None:
    rest = cb.data.split(":", 2)[2]
    code, _, tok = rest.partition(":")
    if not tok:
        # زر من إصدار سابق بلا رمز شراء — لا نخصم من زر قديم؛ نعرض المراجعة من جديد بزر صالح لمرة واحدة
        await cb_buy(cb)
        return
    try:
        subscription = await SD.purchase(cb.from_user.id, code, checkout_key=f"sub-{cb.from_user.id}-{tok}")
    except InsufficientBalance as e:
        await cb.message.edit_text(
            f"❌ رصيدك غير كافٍ.\n\nرصيدك: <b>{fmt(e.balance)}</b>\nالمطلوب: <b>{fmt(e.needed)}</b>",
            reply_markup=K.scheduled_purchase_confirm(code),
        )
        await cb.answer("اشحن رصيدك أولاً", show_alert=True)
        return
    except ValueError as e:
        await cb.answer(str(e), show_alert=True)
        return
    except Exception:
        # لا نُظهر تفاصيل قاعدة البيانات للعميل، لكن نسجل السبب مع المستخدم
        # والباقة حتى يظهر السبب الحقيقي في Render بدلاً من رسالة عامة فقط.
        log.exception("scheduled purchase failed user=%s code=%s", cb.from_user.id, code)
        await cb.answer("تعذر إنشاء الاشتراك الآن. حاول مرة أخرى.", show_alert=True)
        return
    if subscription.get("duplicate"):
        # ضغطة مكررة أو زر قديم: الاشتراك أُنشئ وخُصم مرة واحدة فقط
        await cb.answer(f"✅ اشتراكك SUB-{subscription['id']} مسجّل مسبقاً — لم يُخصم أي مبلغ إضافي")
        return
    await notify.notify_admins_new_subscriber(cb.bot, subscription)
    await cb.message.edit_text(
        f"✅ <b>تم تسجيل اشتراكك</b>\n\n"
        f"الباقة: <b>{_esc(subscription['package_title'])}</b>\n"
        f"رقم الاشتراك: <code>SUB-{subscription['id']}</code>\n"
        f"الطلب: <code>#ORD-{subscription['order_id']}</code>\n\n"
        "سنجهز التصاميم والنصوص الخاصة بك، ثم يبدأ الإرسال اليومي حسب الموعد المتفق عليه.",
        reply_markup=K.scheduled_after_purchase(),
    )
    await cb.answer("تم الاشتراك ✅")


@router.callback_query(F.data == "sub:mine")
async def cb_mine(cb: CallbackQuery) -> None:
    rows = await SR.list_subscriptions(str(cb.from_user.id), limit=10, offset=0)
    rows = [r for r in rows if int(r.get("user_id") or 0) == cb.from_user.id]
    if not rows:
        await cb.message.edit_text("📂 لا توجد لديك باقات تصميم مجدولة بعد.", reply_markup=K.scheduled_package_list(await SD.packages(False)))
        await cb.answer()
        return
    names = {"awaiting_assets": "بانتظار تجهيز التصاميم", "scheduled": "تعمل الآن", "paused": "متوقفة مؤقتاً", "completed": "مكتملة", "cancelled": "ملغاة", "refunded": "مستردة"}
    lines = ["📂 <b>اشتراكاتي</b>", ""]
    for r in rows:
        lines.append(
            f"<code>SUB-{r['id']}</code> · <b>{_esc(r['package_title'])}</b>\n"
            f"الحالة: {names.get(r['status'], r['status'])} · {int(r.get('sent_count') or 0)}/{int(r['total_items'])}"
        )
    await cb.message.edit_text("\n\n".join(lines), reply_markup=K.scheduled_after_purchase())
    await cb.answer()
