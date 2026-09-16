"""قراءة متغيرات البيئة والتحقق منها مرة واحدة عند الإقلاع.

القاعدة: كل ما هو سرّ أو يختلف بين جهازك وRender يأتي من هنا.
كل ما هو "إعداد عمل" (أسعار، محافظ، نسب) يعيش في جدول settings ويُعدَّل من البوت.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # يقرأ ملف .env محلياً؛ في Render المتغيرات تأتي من لوحة التحكم


def _split_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                ids.append(int(part))
            except ValueError:
                print(f"[config] تجاهلت قيمة غير رقمية في ADMIN_IDS: {part!r}", file=sys.stderr)
    return tuple(ids)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    admin_ids: tuple[int, ...]
    mode: str  # "polling" محلياً | "webhook" في Render
    public_url: str  # https://<app>.onrender.com (مطلوب في webhook فقط)
    webhook_secret: str  # جزء من المسار + ترويسة X-Telegram-Bot-Api-Secret-Token
    port: int  # Render يمرّر PORT تلقائياً
    tz: str
    bot_name: str
    nour_ads_token: str  # الخطوة 4 — فارغ الآن
    nour_dry_run: bool  # عندما يكون 1 لا نرسل شيئاً حقيقياً إلى Nour Ads
    support_username: str  # حساب الدعم البشري (بدون @)
    updates_channel: str  # رابط قناة التحديثات (اختياري)
    extra: dict = field(default_factory=dict)

    @property
    def is_webhook(self) -> bool:
        return self.mode == "webhook"

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.webhook_secret}"

    @property
    def webhook_url(self) -> str:
        return self.public_url.rstrip("/") + self.webhook_path


def load_settings() -> Settings:
    errors: list[str] = []

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token or ":" not in bot_token:
        errors.append("BOT_TOKEN مفقود أو غير صحيح (يجب أن يكون بالشكل 123456:ABC-...)")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        errors.append("DATABASE_URL مفقود أو لا يبدأ بـ postgresql://")

    admin_ids = _split_ids(os.getenv("ADMIN_IDS", ""))
    if not admin_ids:
        errors.append("ADMIN_IDS مفقود — ضع معرّف تيليغرام الخاص بك (رقم) حتى تصلك التنبيهات")

    mode = os.getenv("MODE", "polling").strip().lower()
    if mode not in ("polling", "webhook"):
        errors.append("MODE يجب أن يكون polling أو webhook")

    public_url = os.getenv("PUBLIC_URL", "").strip()
    # Render يوفّر RENDER_EXTERNAL_URL تلقائياً — نستخدمه إن لم يُحدَّد PUBLIC_URL
    if not public_url:
        public_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    # تيليغرام يقبل في secret_token الأحرف والأرقام و _ - فقط (1–256) — ننقّي أي رموز أخرى
    raw_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    webhook_secret = "".join(c for c in raw_secret if c.isalnum() or c in "_-")[:200]
    if mode == "webhook":
        if not public_url.startswith("https://"):
            errors.append("PUBLIC_URL مطلوب في وضع webhook ويجب أن يبدأ بـ https:// (أو اترك Render يوفّر RENDER_EXTERNAL_URL)")
        if len(webhook_secret) < 16:
            errors.append("WEBHOOK_SECRET مطلوب في وضع webhook: 16 حرفاً على الأقل من الأحرف والأرقام")

    try:
        port = int(os.getenv("PORT", "10000"))
    except ValueError:
        port = 10000
        errors.append("PORT يجب أن يكون رقماً")

    if errors:
        print("\n❌ لا يمكن تشغيل البوت — أصلح متغيرات البيئة التالية:", file=sys.stderr)
        for e in errors:
            print(f"   • {e}", file=sys.stderr)
        print("   (محلياً: انسخ .env.example إلى .env واملأه — في Render: Environment ← Add)\n", file=sys.stderr)
        sys.exit(1)

    return Settings(
        bot_token=bot_token,
        database_url=database_url,
        admin_ids=admin_ids,
        mode=mode,
        public_url=public_url,
        webhook_secret=webhook_secret,
        port=port,
        tz=os.getenv("TZ", "Asia/Damascus").strip() or "Asia/Damascus",
        bot_name=os.getenv("BOT_NAME", "روّج بوت").strip() or "روّج بوت",
        nour_ads_token=os.getenv("NOUR_ADS_TOKEN", "").strip(),
        nour_dry_run=os.getenv("NOUR_DRY_RUN", "1").strip() != "0",
        support_username=os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@"),
        updates_channel=os.getenv("UPDATES_CHANNEL", "").strip(),
    )


settings = load_settings()
