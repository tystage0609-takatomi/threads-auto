"""インスタのリール自動投稿

その日に Threads へ投稿した文章から動画を作り（make_reel.py）、リールとして投稿する。
GitHub Actions で Threads の投稿のすぐあとに呼ばれる。

使い方:
  python ig_post.py          # 今日 Threads に投稿した分をリールにする
  python ig_post.py <id>     # posts.csv の id を指定（手動テスト用）
  python ig_post.py recruit  # 今日 Threads に投稿した指導員募集をリールにする
  python ig_post.py r<id>    # recruit.csv の id を指定（手動テスト用）
環境変数:
  TOKEN_KEY / INSTAGRAM_ACCESS_TOKEN  トークン（tokens.py を参照）
  DRY_RUN=1  動画は作るが投稿しない
"""

import csv
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import make_reel
import tokens

JST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
POSTS = os.path.join(HERE, "posts.csv")
RECRUIT = os.path.join(HERE, "recruit.csv")
LOG = os.path.join(HERE, "ig_log.csv")
# Metaの「統合ログイン」で作ったシステムユーザートークン（期限なし）を使う
API = "https://graph.facebook.com/v23.0"
IG_USER_ID = "17841463025184235"  # @unten_urakyokasho

HASHTAGS = (
    # 基本の5つ（ユーザー指定）＋追加分
    "#ペーパードライバー講習 #教習所 #自動車 #運転 #交通事故 "
    "#ペーパードライバー #運転のコツ #ドライブプラス #大阪 #兵庫"
)


RECRUIT_HASHTAGS = (
    "#教習指導員 #指導員募集 #ペーパードライバー講習 #出張講習 "
    "#自動車学校 #教習所 #独立開業 #ドライブプラス"
)


def caption(text, kind="daily"):
    lines = []
    for ln in text.strip().splitlines():
        # リンクと本人の名前・署名は入れない
        if "http" in ln or "山田" in ln or "代表指導員" in ln:
            continue
        s = ln.strip()
        if s in ("詳しくはこちら", "詳しくはnoteで"):
            continue
        lines.append(ln)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if kind == "recruit":
        body = body.replace("ホームページの\n「お問い合わせ」フォーム", "プロフィールのリンク（ホームページ）の\n「お問い合わせ」フォーム")
        return f"{body}\n\n▶ プロフィールのリンクからホームページへ\n\n{RECRUIT_HASHTAGS}"
    return (
        f"{body}\n\n"
        "ペーパードライバー講習のドライブプラス\n"
        "▶ くわしくはプロフィールのリンクから\n\n"
        f"{HASHTAGS}"
    )


def read_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_log(rows):
    with open(LOG, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "posted_at", "media_id", "kind"])
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in ["id", "posted_at", "media_id", "kind"]} for r in rows)


def api_err(r, what):
    if not r.ok:
        print(f"::error::インスタ エラー（{what}）: {r.status_code} {r.text}")
    r.raise_for_status()
    return r.json()


def publish_reel(video_path, cap, token):
    uid = IG_USER_ID
    user = api_err(requests.get(f"{API}/{uid}", params={"fields": "username", "access_token": token}, timeout=30), "アカウント確認")
    print(f"@{user['username']} に投稿します")

    # 1. 入れ物を作る（動画は直接アップロードする方式）
    c = api_err(requests.post(f"{API}/{uid}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": cap,
        "share_to_feed": "true", "access_token": token,
    }, timeout=60), "作成")
    cid, upload_uri = c["id"], c["uri"]

    # 2. 動画を送る
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as fh:
        r = requests.post(upload_uri, data=fh.read(), headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
        }, timeout=300)
    api_err(r, "動画アップロード")

    # 3. インスタ側の処理を待つ
    for _ in range(60):
        time.sleep(10)
        s = api_err(requests.get(f"{API}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=30), "状態確認")
        print("処理中:", s.get("status_code"))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            print(f"::error::動画の処理に失敗: {s}")
            sys.exit(1)
    else:
        print("::error::動画の処理が10分で終わりませんでした")
        sys.exit(1)

    # 4. 公開
    p = api_err(requests.post(f"{API}/{uid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60), "公開")
    return p["id"]


def main():
    now = datetime.now(JST)
    today = now.date().isoformat()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    # recruit / r<id> は指導員募集（recruit.csv）、それ以外は毎日の投稿（posts.csv）
    kind = "recruit" if arg == "recruit" or arg.startswith("r") else "daily"
    src = RECRUIT if kind == "recruit" else POSTS
    with open(src, encoding="utf-8-sig", newline="") as f:
        posts = list(csv.DictReader(f))
    log = read_log()

    if arg and arg != "recruit":
        post = next(p for p in posts if p["id"] == arg.lstrip("r"))
    else:
        if any(r["posted_at"].startswith(today) and (r.get("kind") or "daily") == kind for r in log):
            print("本日はインスタに投稿済みのためスキップ")
            return
        todays = [p for p in posts if p.get("posted_at", "").startswith(today)]
        if not todays:
            print("今日の Threads の投稿がないのでスキップ")
            return
        post = todays[-1]

    video = os.path.join(HERE, "reel.mp4")
    sec = make_reel.build(post["text"], video, seed=f"{kind}{post['id']}", kind=kind)
    cap = caption(post["text"], kind)
    print(f"動画を作りました（id={post['id']}、{sec:.1f}秒）\n--- 本文 ---\n{cap}\n------------")

    token = tokens.get("instagram", "INSTAGRAM_ACCESS_TOKEN")
    if os.environ.get("DRY_RUN") == "1":
        if token:
            r = requests.get(f"{API}/{IG_USER_ID}", params={"fields": "username", "access_token": token}, timeout=30)
            print(f"✅ トークン確認OK：@{r.json().get('username')}" if r.ok else f"::error::トークンが使えません: {r.text}")
        print("DRY_RUN のため投稿していません")
        return
    if not token:
        print("::error::インスタのトークンがありません")
        sys.exit(1)

    media_id = publish_reel(video, cap, token)
    log.append({"id": post["id"], "posted_at": now.strftime("%Y-%m-%d %H:%M"), "media_id": media_id, "kind": kind})
    write_log(log)
    print(f"投稿完了 media_id={media_id}")


if __name__ == "__main__":
    main()
