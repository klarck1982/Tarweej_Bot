-- 002_topups.sql — الخطوة 2: الرصيد والشحن
-- طرق الدفع: USDT على TRC20 و BEP20 (قرار 16/09). العناوين تُدخل من لوحة الأدمن وتُحفظ في settings.

-- رقم مرجعي قصير يظهر للعميل والأدمن (TOP-77)
ALTER TABLE topups ADD COLUMN IF NOT EXISTS admin_msg_ids JSONB NOT NULL DEFAULT '[]'::jsonb;  -- رسائل الأدمن لتعطيل أزرارها بعد القرار
ALTER TABLE topups ADD COLUMN IF NOT EXISTS user_msg_id BIGINT;                                 -- رسالة "بانتظار الاعتماد" عند العميل

-- منع أكثر من طلب شحن معلّق واحد لكل مستخدم (يبسّط المطابقة)
CREATE UNIQUE INDEX IF NOT EXISTS topups_one_pending_per_user
    ON topups(user_id) WHERE status = 'pending';

-- إعدادات الدفع الافتراضية (العناوين فارغة حتى يدخلها الأدمن)
INSERT INTO settings (key, value) VALUES
  ('payment_methods', '{
      "usdt_trc20": {"title": "USDT — شبكة TRC20 (Tron)",  "enabled": true, "address": "", "network": "TRC20", "note": "الأرخص رسوماً — الأكثر استخداماً"},
      "usdt_bep20": {"title": "USDT — شبكة BEP20 (BNB Smart Chain)", "enabled": true, "address": "", "network": "BEP20", "note": "مناسب لمحافظ Binance وTrust Wallet"}
  }'::jsonb),
  ('topup_presets_usd', '[5, 10, 20, 50, 100]'::jsonb),
  ('topup_sla_text', '"التأكيد عادةً خلال 30 دقيقة – 3 ساعات (أوقات العمل)."'::jsonb)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
WHERE settings.key = 'payment_methods' AND settings.value = '{}'::jsonb;
