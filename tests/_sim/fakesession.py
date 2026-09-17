"""جلسة وهمية: تلتقط كل ما يرسله البوت بدل الاتصال بتيليغرام (للمحاكاة المحلية)."""
import itertools
from datetime import datetime
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Message, Chat, User

sent = []
mid = itertools.count(1000)
CHANNELS = {-1001000000001: "إيداعات ترويج", -1001000000002: "طلبات ترويج", -1001000000003: "تنبيهات ترويج"}

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
        if name in ("EditMessageReplyMarkup", "SendPhoto", "SendVideo", "SendDocument"):
            return Message(message_id=d.get("message_id") or next(mid), date=datetime.now(), chat=Chat(id=d.get("chat_id", 0), type="private"))
        if name == "SendMediaGroup":
            return [Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=d.get("chat_id", 0), type="private")) for _ in d.get("media", [])]
        if name == "GetChat":
            from aiogram.types import ChatFullInfo
            cid = int(d["chat_id"])
            from aiogram.types import AcceptedGiftTypes
            return ChatFullInfo(id=cid, type="channel" if cid < 0 else "private", title=CHANNELS.get(cid, f"chat{cid}"),
                                accent_color_id=0, max_reaction_count=0,
                                accepted_gift_types=AcceptedGiftTypes(unlimited_gifts=False, limited_gifts=False, unique_gifts=False, premium_subscription=False, gifts_from_channels=False))
        if name == "GetChatMember":
            from aiogram.types import ChatMemberAdministrator
            return ChatMemberAdministrator(user=User(id=1, is_bot=True, first_name="b"), can_be_edited=False, is_anonymous=False,
                                           can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=False, can_restrict_members=False,
                                           can_promote_members=False, can_change_info=False, can_invite_users=False, can_post_stories=False,
                                           can_edit_stories=False, can_delete_stories=False, can_send_welcome_messages=False, can_post_messages=True)
        return True
