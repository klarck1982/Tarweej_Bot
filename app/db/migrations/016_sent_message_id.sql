-- 🎨 باقة تصميم يومي — متابعة رسالة التسليم (016)
-- لتمكين «حذف رسالة المعاينة» من وجه الاشتراك (قناة الزبون أو محادثته) بعد التجربة.
ALTER TABLE scheduled_subscription_items
    ADD COLUMN IF NOT EXISTS sent_message_id BIGINT;
