-- 003_shamcash.sql — إضافة شام كاش (دولار + ليرة سورية) إلى طرق الدفع
-- يُدمج مع الموجود: لا يمسّ عناوين USDT المحفوظة سابقاً، ويضيف الطرق الجديدة إن لم تكن موجودة.
UPDATE settings
SET value = jsonb_build_object(
        'shamcash_usd', jsonb_build_object('kind','shamcash','title','شام كاش — دولار','short','شام كاش $',
                                           'currency','USD','enabled',true,'address','','holder',''),
        'shamcash_syp', jsonb_build_object('kind','shamcash','title','شام كاش — ليرة سورية','short','شام كاش ل.س',
                                           'currency','SYP','enabled',true,'address','','holder','')
    ) || value,
    updated_at = now()
WHERE key = 'payment_methods';

-- سعر صرف الليرة (0 = غير مضبوط → طريقة الليرة مخفية حتى يضبطه الأدمن)
INSERT INTO settings (key, value) VALUES ('syp_per_usd', '0'::jsonb)
ON CONFLICT (key) DO NOTHING;
