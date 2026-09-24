"""البث (v0.9.2): التهريب مطابق للمعاينة + احترام حد تيليغرام 429 — لا يحتاج قاعدة بيانات."""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "1:a")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@h/d")

from aiogram.exceptions import TelegramRetryAfter  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402

from app.bot.handlers.admin import tools as TT  # noqa: E402


def test_render_escapes_text_and_name():
    body = TT.render_broadcast("عرض <b>خاص</b> & خصم {name}", "<سامر>")
    assert body == "عرض &lt;b&gt;خاص&lt;/b&gt; &amp; خصم &lt;سامر&gt;"


def test_render_default_name():
    assert TT.render_broadcast("أهلاً {name}", None) == "أهلاً صديقنا"


class _FakeBot:
    def __init__(self, fail_times: int):
        self.fail_times, self.calls = fail_times, []

    async def send_message(self, chat_id, text):
        self.calls.append(("msg", chat_id, text))
        if len(self.calls) <= self.fail_times:
            raise TelegramRetryAfter(method=SendMessage(chat_id=chat_id, text=text), message="Too Many Requests", retry_after=0)

    async def send_photo(self, chat_id, photo, caption=None):
        self.calls.append(("photo", chat_id, caption))


def test_retry_after_is_respected():
    bot = _FakeBot(fail_times=2)
    asyncio.run(TT._send_with_retry(bot, 5, "hi", None, 2))
    assert len(bot.calls) == 3   # فشل مرتين بـ 429 ثم نجح — لم تضع الرسالة


def test_long_caption_split():
    bot = _FakeBot(fail_times=0)
    asyncio.run(TT._send_with_retry(bot, 5, "x" * 1500, "PHOTO", 1500))
    assert [c[0] for c in bot.calls] == ["photo", "msg"] and bot.calls[0][2] is None
