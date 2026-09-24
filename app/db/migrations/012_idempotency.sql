-- v0.9.2 — منع الخصم المكرر (الضغط المزدوج / الأزرار القديمة / التحديثات المتزامنة)
--
-- orders.checkout_key               : رمز الشراء المولَّد عند عرض الملخص — طلب واحد فقط لكل رمز.
--                                     (idempotency_key يبقى مفتاح الإرسال إلى Nour كما هو)
-- scheduled_subscriptions.checkout_key : نفس الفكرة لشراء باقات التصميم اليومي.
-- idempotency_keys                  : مفاتيح «مرة واحدة فقط» لعمليات الأدمن (تعديل الرصيد يدوياً…).

ALTER TABLE orders ADD COLUMN IF NOT EXISTS checkout_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS orders_checkout_key_uq
    ON orders(checkout_key) WHERE checkout_key IS NOT NULL;

ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS checkout_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS scheduled_subscriptions_checkout_key_uq
    ON scheduled_subscriptions(checkout_key) WHERE checkout_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         TEXT PRIMARY KEY,
    user_id     BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
