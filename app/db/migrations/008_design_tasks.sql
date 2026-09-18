-- v0.8.0 — 🎨 خدمات الكتابة والتصميم + 🛠️ لوحة المهام
-- الطلبات اليدوية تُدار من جدول orders نفسه (مثل القنوات الشريكة) — أعمدة المهلة والتسليم والتعديلات فقط.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS due_at         TIMESTAMPTZ;                        -- موعد التسليم (من الدفع + ساعات الخدمة)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_at   TIMESTAMPTZ;                        -- آخر تسليم للعميل
ALTER TABLE orders ADD COLUMN IF NOT EXISTS approve_by     TIMESTAMPTZ;                        -- الاعتماد التلقائي إن صمت العميل
ALTER TABLE orders ADD COLUMN IF NOT EXISTS revision_count INT NOT NULL DEFAULT 0;             -- عدد التعديلات المطلوبة
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery       JSONB NOT NULL DEFAULT '[]'::jsonb; -- كل التسليمات [{files, text, at, n}]
ALTER TABLE orders ADD COLUMN IF NOT EXISTS due_warned_at  TIMESTAMPTZ;                        -- أُرسل تنبيه «اقترب الموعد»
ALTER TABLE orders ADD COLUMN IF NOT EXISTS late_warned_at TIMESTAMPTZ;                        -- أُرسل تنبيه «متأخر»
CREATE INDEX IF NOT EXISTS orders_due_idx ON orders(due_at) WHERE due_at IS NOT NULL;

-- الهوية البصرية المحفوظة للعميل (لوغو file_id + ألوان) — تُستخدم في طلبات التصميم القادمة
ALTER TABLE users ADD COLUMN IF NOT EXISTS brand_kit JSONB NOT NULL DEFAULT '{}'::jsonb;
