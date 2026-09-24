"""إعداد pytest: قيم وهمية لمتغيرات البيئة حتى تعمل الاختبارات بلا .env.

app/config.py يقرأ البيئة عند الاستيراد ويخرج إن نقصت. نضبط قيماً وهمية هنا قبل أي استيراد
(setdefault لا يطغى على قيم حقيقية، وload_dotenv لا يطغى على ما ضبطناه ← لا اتصال بقاعدة حقيقية بالخطأ).
اختبارات القاعدة تستخدم TEST_DATABASE_URL فقط، وتُتخطى إن لم يُحدَّد.
"""
import os

os.environ.setdefault("BOT_TOKEN", "1:test-token")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("NOUR_DRY_RUN", "1")
