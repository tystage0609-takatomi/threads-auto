"""Threads のアクセストークンを延長する（60日 → また60日）。
インスタはシステムユーザートークン（期限なし）なので延長はいらない。

GitHub Actions から週1回呼ばれる。延長したトークンは tokens.enc に保存する。
"""

import sys
from datetime import datetime, timedelta, timezone

import requests

import tokens

JST = timezone(timedelta(hours=9))

TARGETS = {
    "threads": (
        "THREADS_ACCESS_TOKEN",
        "https://graph.threads.net/refresh_access_token",
        "th_refresh_token",
    ),
}


def main():
    data = tokens.load()
    failed = False
    for name, (env, url, grant) in TARGETS.items():
        tok = tokens.get(name, env)
        if not tok:
            print(f"{name}: トークンがないのでスキップ")
            continue
        r = requests.get(url, params={"grant_type": grant, "access_token": tok}, timeout=30)
        if not r.ok:
            print(f"::error::{name} のトークン延長に失敗: {r.status_code} {r.text}")
            failed = True
            continue
        j = r.json()
        days = int(j.get("expires_in", 0)) // 86400
        until = (datetime.now(JST) + timedelta(days=days)).strftime("%Y-%m-%d")
        data[name] = {"token": j["access_token"], "expires": until}
        print(f"{name}: 延長しました（{until} まで有効）")
    tokens.save(data)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
