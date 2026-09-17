-- 004_orders.sql — الخطوة 3: طلبات إعلانات فيسبوك/إنستغرام (المعالج + بطاقة الأدمن + المحاكاة)
-- إضافات فقط على جدول orders الموجود منذ 001 — آمنة للتكرار.

-- من هو المسؤول عن آخر تغيير (أدمن / النظام / نور) + سبب/ملاحظة
ALTER TABLE orders ADD COLUMN IF NOT EXISTS note           TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS admin_id       BIGINT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS admin_msg_ids  JSONB NOT NULL DEFAULT '[]'::jsonb;  -- بطاقات الأدمن لتحديثها بعد كل تغيير
ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_msg_id    BIGINT;                              -- رسالة الحالة الأخيرة عند العميل
ALTER TABLE orders ADD COLUMN IF NOT EXISTS nour_payload   JSONB;                               -- ما أُرسل/سيُرسل إلى نور حرفياً
ALTER TABLE orders ADD COLUMN IF NOT EXISTS nour_response  JSONB;                               -- آخر ردّ من نور (أو المحاكاة)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS submit_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS next_retry_at  TIMESTAMPTZ;                         -- إعادة المحاولة عند نقص رصيد نور
ALTER TABLE orders ADD COLUMN IF NOT EXISTS last_sync_at   TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS started_at     TIMESTAMPTZ;                         -- لحظة active
ALTER TABLE orders ADD COLUMN IF NOT EXISTS refunded_usd   NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS expires_at     TIMESTAMPTZ;                         -- المسودة تنتهي بعد 7 أيام

-- مسودة واحدة "بانتظار الدفع" لكل مستخدم (يستأنفها بعد الشحن)
CREATE UNIQUE INDEX IF NOT EXISTS orders_one_awaiting_per_user
    ON orders(user_id) WHERE status = 'awaiting_payment';
CREATE INDEX IF NOT EXISTS orders_retry_idx ON orders(next_retry_at) WHERE next_retry_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS orders_nour_idx  ON orders(nour_id) WHERE nour_id IS NOT NULL;

-- ملفات العميل (صور/فيديو المنشور) — file_id فقط، لا تنزيل
CREATE TABLE IF NOT EXISTS order_media (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('photo','video','document')),
    file_id     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_media_order_idx ON order_media(order_id);

-- إعدادات الخطوة 3
INSERT INTO settings (key, value) VALUES
  ('nour_low_balance_usd', '50'::jsonb),          -- تنبيه الأدمن عندما يقلّ رصيد نور عن هذا
  ('order_draft_days', '7'::jsonb),               -- عمر المسودة بانتظار الدفع
  ('order_charge_tolerance_usd', '0.05'::jsonb),  -- الفرق المقبول بين charged المتوقع والفعلي
  ('admin_fallback_username', '""'::jsonb)        -- معرّف الأدمن الاحتياطي عندما لا يملك العميل معرّفاً (بدون @)
ON CONFLICT (key) DO NOTHING;
