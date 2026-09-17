-- 005_tg_ads.sql — الخطوة 5أ: إعلانات Telegram Ads الرسمية (تنفيذ يدوي من حساب الأدمن)
-- إضافات فقط — آمنة للتكرار.

-- نتائج الحملة (تُدخل يدوياً عند الإغلاق) + سبب رفض تيليغرام + نص بديل
ALTER TABLE orders ADD COLUMN IF NOT EXISTS results        JSONB;          -- {"views": 12000, "clicks": 340, "spent_ton": 8.5}
ALTER TABLE orders ADD COLUMN IF NOT EXISTS revision_note  TEXT;           -- سبب طلب تعديل النص (من تيليغرام/الأدمن)

-- حالة جديدة: needs_revision (النص مرفوض — ينتظر نصاً بديلاً من العميل)
CREATE INDEX IF NOT EXISTS orders_kind_status_idx ON orders(kind, status);

INSERT INTO settings (key, value) VALUES
  ('tg_ads_review_hours', '"1 – 24"'::jsonb)     -- نص مدة مراجعة تيليغرام المعروض للعميل
ON CONFLICT (key) DO NOTHING;
