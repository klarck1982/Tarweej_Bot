"""بيئة محاكاة افتراضية صارمة لـ «ترويج بوت».

- StrictSession: تيليغرام وهمي يلتقط كل طلب ويتحقق من قيود تيليغرام الحقيقية
  (HTML صالح، طول النص/التعليق، callback_data ≤ 64 بايت، نص تنبيه ≤ 200، أزرار صالحة).
- Issues: مجمّع المشاكل (استثناءات، رسائل خطأ، أزرار ميتة، ضغطات بلا رد، انتهاكات قيود تيليغرام).
- Actor: مستخدم افتراضي يرسل نصوصاً/صوراً/ملفات ويضغط الأزرار ويقرأ آخر لوحة أزرار.
"""
from __future__ import annotations

import asyncio, itertools, json, logging, os, re, sys, traceback
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

A, U, U2, U3 = 999, 555, 556, 557          # أدمن، عميل غني، عميل فقير، عميل باسم «خبيث»
ADMIN2 = 998
os.environ.update(
    BOT_TOKEN="123456:TESTTOKEN",
    DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/promobot?sslmode=disable"),
    ADMIN_IDS=f"{A},{ADMIN2}", MODE="polling", BOT_NAME="ترويج بوت", NOUR_DRY_RUN="1",
    SUPPORT_USERNAME="support_demo", TZ="Asia/Damascus",
)

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import TelegramMethod
from aiogram.types import (CallbackQuery, Chat, Document, Message, MessageId, PhotoSize, Update, User, Video,
                           File)

# ───────────────────────── مجمّع المشاكل ─────────────────────────

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Issue:
    sev: str
    kind: str
    where: str
    detail: str
    role: str = ""


class Issues:
    def __init__(self):
        self.items: list[Issue] = []
        self.context = "-"
        self.role = "-"
        self._seen: set[tuple] = set()

    def add(self, sev, kind, detail, where=None):
        where = where or self.context
        key = (kind, re.sub(r"\d+", "#", where), re.sub(r"\d+", "#", detail)[:160])
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append(Issue(sev, kind, where, detail, self.role))
        print(f"   ⚠️ [{sev}] {kind} @ {where}: {detail[:300]}")


ISSUES = Issues()


class _LogCatcher(logging.Handler):
    """أي log بمستوى ERROR (مثل unhandled error في ErrorsMiddleware) = مشكلة."""

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            tb = ""
            if record.exc_info:
                tb = "".join(traceback.format_exception(*record.exc_info)[-3:])
            ISSUES.add("high", "exception", f"{record.getMessage()[:200]} | {tb.strip()[-600:]}")
        elif record.levelno >= logging.WARNING and "slow handler" not in record.getMessage():
            ISSUES.add("low", "warning-log", f"{record.name}: {record.getMessage()[:250]}")


logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger().addHandler(_LogCatcher())
logging.getLogger("aiogram.event").setLevel(logging.WARNING)

# ───────────────────────── التحقق من HTML تيليغرام ─────────────────────────

ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler", "a", "tg-emoji",
           "code", "pre", "blockquote"}
_ENTITY = re.compile(r"&(lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);")


class _TgHtml(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"وسم غير مدعوم <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"وسم إغلاق غير متوازن </{tag}> (المفتوح: {self.stack[-1:] })")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def check_html(text: str) -> list[str]:
    errs = []
    # < لا يتبعه وسم صالح = تيليغرام يرفض الرسالة كاملة (Bad Request: can't parse entities)
    for m in re.finditer(r"<", text):
        rest = text[m.start():m.start() + 40]
        if not re.match(r"</?[a-zA-Z][a-zA-Z0-9-]*(\s[^<>]*)?>", rest):
            errs.append(f"«<» غير مهرَّب: {rest[:25]!r}")
    for m in re.finditer(r"&", text):
        if not _ENTITY.match(text, m.start()):
            errs.append(f"«&» غير مهرَّب: {text[m.start():m.start()+15]!r}")
    p = _TgHtml()
    try:
        p.feed(text); p.close()
    except Exception as e:  # noqa: BLE001
        errs.append(f"HTML parser: {e}")
    errs += p.errors
    if p.stack:
        errs.append(f"وسوم لم تُغلق: {p.stack}")
    return errs


def visible_len(text: str) -> int:
    """طول النص كما يعدّه تيليغرام بعد إزالة الوسوم (تقريبي: UTF-16)."""
    t = re.sub(r"<[^>]+>", "", text)
    t = t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return len(t.encode("utf-16-le")) // 2

# ───────────────────────── تيليغرام الوهمي الصارم ─────────────────────────

sent: list[tuple[str, dict]] = []
_mid = itertools.count(100_000)
_fid = itertools.count(1)
CHANNELS = {-1001000000001: "إيداعات ترويج", -1001000000002: "طلبات ترويج", -1001000000003: "تنبيهات ترويج"}
BLOCKED_CHATS: set[int] = set()   # محاكاة مستخدم حظر البوت (Forbidden)


def _kb_buttons(markup) -> list[dict]:
    if not markup or not isinstance(markup, dict):
        return []
    rows = markup.get("inline_keyboard") or []
    return [b for row in rows for b in row]


class StrictSession(BaseSession):
    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        if False:
            yield b""

    def _validate(self, name: str, d: dict):
        parse = d.get("parse_mode", "HTML")
        for fld, limit in (("text", 4096), ("caption", 1024)):
            val = d.get(fld)
            if val is None:
                continue
            if isinstance(val, str):
                if not val.strip():
                    ISSUES.add("medium", "empty-text", f"{name}.{fld} فارغ — تيليغرام يرفض")
                if parse == "HTML":
                    for e in check_html(val)[:2]:
                        ISSUES.add("high", "bad-html", f"{name}.{fld}: {e} | نص: {re.sub(chr(10),' / ', val)[:160]!r}")
                    L = visible_len(val)
                else:
                    L = len(val.encode('utf-16-le')) // 2
                if L > limit:
                    ISSUES.add("high", "too-long", f"{name}.{fld} طوله {L} > {limit} — تيليغرام يرفض")
        for b in _kb_buttons(d.get("reply_markup")):
            txt = b.get("text", "")
            if not txt.strip():
                ISSUES.add("medium", "empty-button", f"زر بلا نص في {name}")
            cd = b.get("callback_data")
            if cd is not None:
                n = len(cd.encode())
                if n > 64:
                    ISSUES.add("high", "callback-too-long", f"callback_data {n} بايت > 64: {cd!r} (زر {txt!r})")
                if n == 0:
                    ISSUES.add("high", "callback-empty", f"زر {txt!r} callback_data فارغ")
            url = b.get("url")
            if url is not None and not re.match(r"^(https?|tg)://", url):
                ISSUES.add("high", "bad-url-button", f"رابط زر غير صالح {url!r} (زر {txt!r})")
            wa = b.get("web_app")
            if wa and not str(wa.get("url", "")).startswith("https://"):
                ISSUES.add("medium", "webapp-url", f"web_app يتطلب https: {wa}")
        if name == "AnswerCallbackQuery" and d.get("text") and len(d["text"]) > 200:
            ISSUES.add("medium", "alert-too-long", f"نص تنبيه الضغطة {len(d['text'])} > 200: {d['text'][:80]!r}")
        if name == "SendMediaGroup" and not (2 <= len(d.get("media", [])) <= 10):
            ISSUES.add("high", "media-group-size", f"SendMediaGroup بعدد {len(d.get('media', []))} (يجب 2–10)")

    def _msg(self, d, **extra):
        cid = d.get("chat_id", 0)
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            cid = -100999
        return Message(message_id=next(_mid), date=datetime.now(),
                       chat=Chat(id=cid, type="private" if cid > 0 else "channel"),
                       from_user=User(id=123456, is_bot=True, first_name="bot"), **extra)

    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        from aiogram.exceptions import TelegramForbiddenError
        name = type(method).__name__
        d = method.model_dump(exclude_none=True)
        from aiogram.client.default import Default
        for k, v in list(d.items()):
            if isinstance(v, Default):
                d[k] = "HTML" if k == "parse_mode" else None
        sent.append((name, d))
        self._validate(name, d)
        cid = d.get("chat_id")
        if isinstance(cid, int) and cid in BLOCKED_CHATS and name.startswith(("Send", "Copy", "Forward")):
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if name == "GetMe":
            return User(id=123456, is_bot=True, first_name="ترويج", username="tarweej_test_bot")
        if name in ("SendMessage", "EditMessageText"):
            return self._msg(d, text=d.get("text"))
        if name in ("SendPhoto",):
            return self._msg(d, caption=d.get("caption"), photo=[PhotoSize(file_id=f"sentph{next(_fid)}", file_unique_id="u", width=1, height=1)])
        if name in ("SendDocument",):
            return self._msg(d, caption=d.get("caption"), document=Document(file_id=f"sentdoc{next(_fid)}", file_unique_id="u"))
        if name in ("SendVideo",):
            return self._msg(d, caption=d.get("caption"), video=Video(file_id=f"sentvid{next(_fid)}", file_unique_id="u", width=1, height=1, duration=1))
        if name in ("EditMessageCaption", "EditMessageReplyMarkup", "SendAnimation", "SendAudio", "SendVoice"):
            return self._msg(d, caption=d.get("caption"))
        if name == "SendMediaGroup":
            return [self._msg(d) for _ in d.get("media", [])]
        if name in ("CopyMessage",):
            return MessageId(message_id=next(_mid))
        if name == "ForwardMessage":
            return self._msg(d)
        if name == "GetFile":
            return File(file_id=d["file_id"], file_unique_id="u", file_path="x/y.jpg")
        if name == "GetChat":
            from aiogram.types import AcceptedGiftTypes, ChatFullInfo
            c = int(d["chat_id"]) if str(d["chat_id"]).lstrip("-").isdigit() else -100777
            return ChatFullInfo(id=c, type="channel" if c < 0 else "private", title=CHANNELS.get(c, f"chat{c}"),
                                accent_color_id=0, max_reaction_count=0,
                                accepted_gift_types=AcceptedGiftTypes(unlimited_gifts=False, limited_gifts=False,
                                                                      unique_gifts=False, premium_subscription=False,
                                                                      gifts_from_channels=False))
        if name == "GetChatMember":
            from aiogram.types import ChatMemberAdministrator
            return ChatMemberAdministrator(user=User(id=123456, is_bot=True, first_name="b"), can_be_edited=False,
                                           is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                                           can_manage_video_chats=False, can_restrict_members=False,
                                           can_promote_members=False, can_change_info=False, can_invite_users=False,
                                           can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
                                           can_send_welcome_messages=False, can_post_messages=True)
        if name == "GetChatMemberCount":
            return 1234
        return True

# ───────────────────────── الممثّل (مستخدم افتراضي) ─────────────────────────

_upd = itertools.count(1)
_umid = itertools.count(10)

NAMES = {A: ("رأفت", "admin_raafat"), ADMIN2: ("مازن", "admin2"), U: ("سامر", "samer"), U2: ("ليان", None),
         U3: ('Sam <3 & "Co" <b>', "evil_user")}

FALLBACK_BTN = "هذا الزر غير متاح حالياً"
GENERIC_ERR = "حدث خطأ مؤقت"


def tg_user(uid):
    fn, un = NAMES.get(uid, (f"u{uid}", f"u{uid}"))
    return User(id=uid, is_bot=False, first_name=fn, username=un, language_code="ar")


class Actor:
    def __init__(self, env: "Env", uid: int, role: str):
        self.env, self.uid, self.role = env, uid, role
        self.last_buttons: list[dict] = []     # أزرار آخر رسالة
        self.all_buttons: list[dict] = []      # كل الأزرار التي رآها (لإعادة الضغط لاحقاً = أزرار قديمة)
        self.last_texts: list[str] = []
        self.last_msg_id = None

    # ——— أحداث ———
    def _message(self, **kw):
        return Message(message_id=next(_umid), date=datetime.now(), chat=Chat(id=self.uid, type="private"),
                       from_user=tg_user(self.uid), **kw)

    async def text(self, t: str, label: str | None = None):
        return await self._feed(Update(update_id=next(_upd), message=self._message(text=t)), label or f"✍️ {t[:40]!r}")

    async def photo(self, caption: str | None = None, label=None):
        ph = [PhotoSize(file_id=f"photo_{self.uid}_{next(_fid)}", file_unique_id=f"p{next(_fid)}", width=800, height=800)]
        return await self._feed(Update(update_id=next(_upd), message=self._message(photo=ph, caption=caption)), label or "🖼️ صورة")

    async def document(self, name="file.pdf", mime="application/pdf", label=None, caption=None):
        doc = Document(file_id=f"doc_{self.uid}_{next(_fid)}", file_unique_id=f"d{next(_fid)}", file_name=name, mime_type=mime, file_size=12345)
        return await self._feed(Update(update_id=next(_upd), message=self._message(document=doc, caption=caption)), label or f"📎 {name}")

    async def video(self, label=None):
        v = Video(file_id=f"vid_{self.uid}_{next(_fid)}", file_unique_id=f"v{next(_fid)}", width=720, height=1280, duration=15)
        return await self._feed(Update(update_id=next(_upd), message=self._message(video=v)), label or "🎬 فيديو")

    async def sticker(self):
        from aiogram.types import Sticker
        st = Sticker(file_id="stk", file_unique_id="stk", type="regular", width=1, height=1, is_animated=False, is_video=False)
        return await self._feed(Update(update_id=next(_upd), message=self._message(sticker=st)), "🧸 ملصق")

    async def click(self, data: str, label: str | None = None, chat_id: int | None = None, expect_ok=True):
        chat = chat_id or self.uid
        m = Message(message_id=self.last_msg_id or next(_umid), date=datetime.now(),
                    chat=Chat(id=chat, type="private" if chat > 0 else "channel"),
                    from_user=User(id=123456, is_bot=True, first_name="bot"), text="x")
        cq = CallbackQuery(id=str(next(_upd)), from_user=tg_user(self.uid), chat_instance="ci", message=m, data=data)
        return await self._feed(Update(update_id=next(_upd), callback_query=cq), label or f"🔘 {data}", is_click=True,
                                cb_data=data, expect_ok=expect_ok)

    async def press(self, needle: str, label=None):
        """اضغط زراً من آخر لوحة يحتوي نصه على needle."""
        for b in self.last_buttons:
            if needle in b.get("text", "") and b.get("callback_data"):
                return await self.click(b["callback_data"], label or f"🔘 «{b['text'][:40]}»")
        ISSUES.add("medium", "button-missing", f"زر يحتوي «{needle}» غير موجود. المتاح: {[b.get('text','')[:25] for b in self.last_buttons][:12]}")
        return []

    def has_button(self, needle: str) -> bool:
        return any(needle in b.get("text", "") for b in self.last_buttons)

    def find_cb(self, prefix: str) -> str | None:
        for b in self.last_buttons:
            if (b.get("callback_data") or "").startswith(prefix):
                return b["callback_data"]
        return None

    async def state(self):
        key = StorageKey(bot_id=self.env.bot.id, chat_id=self.uid, user_id=self.uid)
        return await self.env.dp.storage.get_state(key)

    # ——— التغذية والتحليل ———
    async def _feed(self, update: Update, label: str, is_click=False, cb_data=None, expect_ok=True):
        ISSUES.role = self.role
        ISSUES.context = f"{self.role}: {label}"
        start = len(sent)
        fresh = bool(cb_data) and any(b.get("callback_data") == cb_data for b in self.last_buttons)
        try:
            await asyncio.wait_for(self.env.dp.feed_update(self.env.bot, update), timeout=20)
        except asyncio.TimeoutError:
            ISSUES.add("high", "timeout", "المعالج لم ينتهِ خلال 20 ثانية")
        except Exception as e:  # noqa: BLE001
            ISSUES.add("critical", "crash", f"{type(e).__name__}: {e}")
        out = sent[start:]
        self._analyse(out, is_click, cb_data, expect_ok and fresh)
        if self.env.verbose:
            self.env.log(label, out, self.uid)
        return out

    def _analyse(self, out, is_click, cb_data, expect_ok):
        mine = [(n, d) for n, d in out if str(d.get("chat_id")) == str(self.uid)]
        texts = [(d.get("text") or d.get("caption") or "") for n, d in mine]
        for n, d in mine:
            btns = _kb_buttons(d.get("reply_markup"))
            if btns and n in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageReplyMarkup", "EditMessageCaption", "SendDocument", "SendVideo"):
                self.last_buttons = btns
                self.all_buttons += btns
        if texts:
            self.last_texts = texts
        for t in texts:
            if GENERIC_ERR in t:
                ISSUES.add("high", "user-saw-error", f"المستخدم رأى «{GENERIC_ERR}»")
        alerts = [d for n, d in out if n == "AnswerCallbackQuery"]
        if is_click:
            if not alerts:
                ISSUES.add("low", "unanswered-callback", f"الضغطة {cb_data!r} لم يُرد عليها بـ answerCallbackQuery (دوّامة تحميل ~15ث)")
            if len(alerts) > 1:
                ISSUES.add("low", "double-answer", f"الضغطة {cb_data!r} أُجيبت {len(alerts)} مرات (تيليغرام يرفض الثانية)")
            if any(FALLBACK_BTN in (a.get("text") or "") for a in alerts) and expect_ok:
                ISSUES.add("medium", "dead-button", f"الزر {cb_data!r} لم يلتقطه أي معالج (fallback)")
            if not out:
                ISSUES.add("medium", "no-response", f"الضغطة {cb_data!r} لم تنتج أي رد")
        for n, d in out:
            t = d.get("text") or ""
            if "خطأ غير متوقع" in t:
                ISSUES.add("high", "admin-error-alert", t[:250].replace("\n", " / "))


class Env:
    def __init__(self, verbose=False, logfile=None):
        self.verbose = verbose
        self.logfile = open(logfile, "w", encoding="utf-8") if logfile else None
        self.bot = None
        self.dp = None

    def log(self, label, out, uid):
        lines = [f"\n▶ [{uid}] {label}"]
        for n, d in out:
            if n in ("SendMessage", "EditMessageText", "SendPhoto", "SendDocument", "SendVideo", "EditMessageCaption", "AnswerCallbackQuery", "SendMediaGroup", "CopyMessage"):
                t = (d.get("text") or d.get("caption") or "").replace("\u2800", "").replace("\u200b", "")
                t = re.sub(r"\s+", " ", t)[:260]
                btns = [b.get("text", "")[:22] for b in _kb_buttons(d.get("reply_markup"))][:10]
                lines.append(f"   {n}→{d.get('chat_id', '')}: {t}" + (f"  [{' | '.join(btns)}]" if btns else ""))
        s = "\n".join(lines)
        if self.logfile:
            self.logfile.write(s + "\n"); self.logfile.flush()

    async def boot(self, wipe=True):
        from app.db import pool as db
        from app.main import build_dispatcher
        from app.bot.wide import WideButtonsMiddleware
        from app.services import cpanel, pricing
        await db.init_pool(os.environ["DATABASE_URL"])
        await db.run_migrations()
        if wipe:
            await wipe_db()
        await pricing.refresh(); await cpanel.refresh_runtime()
        self.bot = Bot("123456:TESTTOKEN", session=StrictSession(), default=DefaultBotProperties(parse_mode="HTML"))
        self.bot.session.middleware(WideButtonsMiddleware())
        self.dp = build_dispatcher()
        return self

    def actor(self, uid, role):
        return Actor(self, uid, role)

    async def close(self):
        from app.db import pool as db
        await db.close_pool()
        if self.logfile:
            self.logfile.close()


async def wipe_db():
    from app.db import pool as db
    from app.db.repo import settings as srepo
    tables = [r["tablename"] for r in await db.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('schema_migrations','_migrations')")]
    keep = {t for t in tables if "migration" in t}
    todo = [t for t in tables if t not in keep and t != "settings"]
    if todo:
        await db.execute("TRUNCATE " + ", ".join(todo) + " RESTART IDENTITY CASCADE")
    # إعادة بذر الإعدادات الافتراضية بإعادة تشغيل ترحيلات البذر
    try:
        srepo.invalidate()
    except Exception:  # noqa: BLE001
        pass


def report(path: str, title: str):
    items = sorted(ISSUES.items, key=lambda i: (SEV_ORDER[i.sev], i.kind))
    with open(path, "w", encoding="utf-8") as f:
        json.dump([i.__dict__ for i in items], f, ensure_ascii=False, indent=1)
    print(f"\n══ {title}: {len(items)} مشكلة ══")
    from collections import Counter
    c = Counter((i.sev, i.kind) for i in items)
    for (s, k), n in sorted(c.items(), key=lambda x: SEV_ORDER[x[0][0]]):
        print(f"  {s:8} {k:24} ×{n}")
