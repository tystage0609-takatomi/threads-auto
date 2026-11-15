"""アクセストークンの保管庫。

トークンは tokens.enc に暗号化して保存する（鍵は GitHub の Secret「TOKEN_KEY」）。
GitHub Actions が自動でトークンを更新して書き戻せるように、Secret ではなくファイルに置いている。
"""

import json
import os

from cryptography.fernet import Fernet

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.enc")


def _fernet():
    key = os.environ.get("TOKEN_KEY")
    return Fernet(key.encode()) if key else None


def load():
    f = _fernet()
    if not f or not os.path.exists(PATH):
        return {}
    with open(PATH, "rb") as fh:
        return json.loads(f.decrypt(fh.read()))


def save(data):
    f = _fernet()
    if not f:
        raise RuntimeError("TOKEN_KEY がありません")
    with open(PATH, "wb") as fh:
        fh.write(f.encrypt(json.dumps(data).encode()))


def get(name, env_fallback=None):
    """保管庫のトークン。なければ環境変数（最初の1回用）"""
    tok = load().get(name, {}).get("token")
    if tok:
        return tok
    return os.environ.get(env_fallback) if env_fallback else None
