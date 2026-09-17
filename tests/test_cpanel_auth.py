"""التحقق من توقيع Mini App (initData) — نبني توقيعاً صحيحاً بنفس خوارزمية تيليغرام ونتأكد أن التزوير يُرفض."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from app.services import cpanel as CP

TOKEN = "123456:TESTTOKEN"


def _sign(params: dict, token: str = TOKEN) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**params, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def test_valid_init_data():
    user = json.dumps({"id": 999, "first_name": "أدمن"}, ensure_ascii=False)
    data = _sign({"auth_date": str(int(time.time())), "query_id": "AAH", "user": user})
    u = CP.verify_init_data(data, TOKEN)
    assert u and u["id"] == 999


def test_tampered_and_stale_rejected():
    user = json.dumps({"id": 999})
    good = _sign({"auth_date": str(int(time.time())), "user": user})
    assert CP.verify_init_data(good.replace("999", "998"), TOKEN) is None      # عُدّل المستخدم
    assert CP.verify_init_data(good, "123456:OTHER") is None                  # توكن مختلف
    stale = _sign({"auth_date": str(int(time.time()) - 7200), "user": user})
    assert CP.verify_init_data(stale, TOKEN) is None                          # أقدم من ساعة
    assert CP.verify_init_data("", TOKEN) is None and CP.verify_init_data("hash=abc", TOKEN) is None
