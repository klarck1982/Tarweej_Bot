-- 015_design_queue.sql — «باقة تصميم يومي»: نموذج طابور الأزواج + وجه التسليم (محادثة/قناة)
--
-- • كل زوج (تصميم+نص) يدخل طابور الاشتراك (seq تلقائي FIFO) ويُسلَّم واحداً يومياً في ساعة الاشتراك.
-- • وجه التسليم: محادثة الزبون (target_chat_id=user_id) أو قناة خاصة له (البوت مشرفاً فيها).
-- • الاشتراكات اليدوية (زبائن يدفعون برّا البوت): بلا user_id/order_id.
-- • missed_slot: فات موعد والطابور كان فارغاً ← أول زوج يصل يُرسل فوراً مع اعتذار.
-- • فهرس فريد: وجه واحد فقط لاشتراك نشط (يمنع ربط قناة زبونين معاً بالخطأ).

ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS target_chat_id BIGINT;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS target_kind   TEXT NOT NULL DEFAULT 'user';
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS target_title  TEXT;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS customer_name TEXT;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS is_manual     BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS missed_slot   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS buffer_alert_sent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE scheduled_subscriptions ADD COLUMN IF NOT EXISTS note TEXT;

-- اشتراكات يدوية بلا حساب بوت
ALTER TABLE scheduled_subscriptions ALTER COLUMN user_id DROP NOT NULL;

-- الأقدم: وجه التسليم = محادثة المستخدم نفسه (user_id)، واسم الزبون من جدول المستخدمين.
-- (إصلاح 26/09: النسخة السابقة كانت تشير لعمود user_name غير الموجود في هذا الجدول
--  فكانت تفشل كامل الترحيلة وتمنع إقلاع البوت — راجع سجل Render: UndefinedColumnError)
UPDATE scheduled_subscriptions SET target_chat_id = user_id
 WHERE target_chat_id IS NULL AND user_id IS NOT NULL;

UPDATE scheduled_subscriptions s SET customer_name = u.name
  FROM users u
 WHERE s.customer_name IS NULL AND s.user_id IS NOT NULL
   AND u.tg_id = s.user_id AND COALESCE(u.name, '') <> '';

-- وجه واحد فقط لكل اشتراك نشط — أي محاولة ربط قناة زبونين تفشل بالقاعدة نفسها.
-- ملاحظة متانة: إن وُجدت صفوف تجريبية مكررة على الوجه نفسه، نكتفي بفهرس عادي
-- حتى لا يفشل النشر — والحماية تبقى في طبقة التطبيق (active_on_target).
-- بعد تنظيف التكرار أعد إنشاء الفهرس الفريد يدوياً.
DO $$ BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS scheduled_subscriptions_active_target_uq
        ON scheduled_subscriptions(target_chat_id)
        WHERE status IN ('awaiting_assets','scheduled','paused') AND target_chat_id IS NOT NULL;
EXCEPTION WHEN unique_violation THEN
    CREATE INDEX IF NOT EXISTS scheduled_subscriptions_active_target_idx
        ON scheduled_subscriptions(target_chat_id)
        WHERE status IN ('awaiting_assets','scheduled','paused') AND target_chat_id IS NOT NULL;
END $$;

-- طابور الأزواج: seq = ترتيب الإرسال (FIFO) يُولَّد تلقائياً عند الإدخال
CREATE INDEX IF NOT EXISTS scheduled_items_queue_idx
    ON scheduled_subscription_items(subscription_id, status, seq);
