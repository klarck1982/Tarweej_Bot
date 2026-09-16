"""جلسة وهمية: تلتقط كل ما يرسله البوت بدل الاتصال بتيليغرام (للمحاكاة المحلية)."""
import itertools
from datetime import datetime
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Message, Chat, User

sent = []
mid = itertools.count(1000)

class FakeSession(BaseSession):
    async def close(self): pass
    async def stream_content(self, *a, **k):
        if False: yield b""
    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        name = type(method).__name__
        d = method.model_dump(exclude_none=True)
        sent.append((name, d))
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="RawwejBot", username="rawwej_test_bot")
        if name in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageCaption"):
            return Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=d.get("chat_id", 0), type="private"), text=d.get("text") or d.get("caption"))
        if name in ("EditMessageReplyMarkup",):
            return Message(message_id=d.get("message_id", 1), date=datetime.now(), chat=Chat(id=d.get("chat_id", 0), type="private"))
        return True
