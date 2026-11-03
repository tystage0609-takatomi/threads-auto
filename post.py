"""Threads 自動投稿スクリプト

posts.csv の「未投稿」の先頭1件を Threads に投稿し、投稿日時を記録する。
GitHub Actions から1日2回（7:00 / 9:00 JST）呼ばれる。
  - 平日（祝日以外）… 7:00 の回だけ投稿
  - 土日祝         … 9:00 の回だけ投稿

使い方:
  python post.py weekday   # 7:00 の回
  python post.py weekend   # 9:00 の回
  python post.py test      # 曜日に関係なく投稿（手動テスト用）
環境変数:
  THREADS_ACCESS_TOKEN  Threads のアクセストークン（必須）
  DRY_RUN=1             実際には投稿せず内容だけ表示
"""

import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import jpholiday
import requests

JST = timezone(timedelta(hours=9))
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posts.csv")
API = "https://graph.threads.net/v1.0"
MAX_LEN = 500  # Threads の文字数上限
FIELDS = ["id", "text", "posted_at"]


def is_offday(d):
    return d.weekday() >= 5 or jpholiday.is_holiday(d)


def load_posts():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_posts(rows):
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def publish(text, token):
    r = requests.post(
        f"{API}/me/threads",
        data={"media_type": "TEXT", "text": text, "access_token": token},
        timeout=30,
    )
    r.raise_for_status()
    creation_id = r.json()["id"]
    time.sleep(5)
    r = requests.post(
        f"{API}/me/threads_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def main():
    slot = sys.argv[1] if len(sys.argv) > 1 else "test"
    now = datetime.now(JST)
    today = now.date()
    off = is_offday(today)

    if slot == "weekday" and off:
        print(f"{today} は土日祝のため 7:00 の回はスキップ")
        return
    if slot == "weekend" and not off:
        print(f"{today} は平日のため 9:00 の回はスキップ")
        return

    rows = load_posts()

    # 同じ日に二重投稿しない
    if slot != "test" and any(r.get("posted_at", "").startswith(today.isoformat()) for r in rows):
        print("本日は投稿済みのためスキップ")
        return

    pending = [r for r in rows if not r.get("posted_at", "").strip()]
    if not pending:
        print("::error::投稿ストックが空です。posts.csv に投稿を追加してください")
        sys.exit(1)

    post = pending[0]
    text = post["text"].strip()
    if len(text) > MAX_LEN:
        print(f"::error::id={post['id']} が {len(text)} 文字で上限 {MAX_LEN} を超えています")
        sys.exit(1)

    print(f"投稿します (id={post['id']}):\n{text}\n")

    if os.environ.get("DRY_RUN") == "1":
        token = os.environ.get("THREADS_ACCESS_TOKEN")
        if token:
            r = requests.get(f"{API}/me", params={"fields": "username", "access_token": token}, timeout=30)
            if r.ok:
                print(f"✅ トークン確認OK：@{r.json().get('username')} に投稿できます")
            else:
                print(f"::error::トークンが使えません: {r.text}")
                sys.exit(1)
        print("DRY_RUN のため実際には投稿していません")
        return

    token = os.environ["THREADS_ACCESS_TOKEN"]
    thread_id = publish(text, token)
    post["posted_at"] = now.strftime("%Y-%m-%d %H:%M")
    save_posts(rows)
    print(f"投稿完了 thread_id={thread_id}")

    remaining = len(pending) - 1
    print(f"残りストック: {remaining} 件")
    if remaining <= 7:
        print(f"::warning::投稿ストック残り {remaining} 件です。追加してください")


if __name__ == "__main__":
    main()
