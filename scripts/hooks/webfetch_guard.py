#!/usr/bin/env python3
"""WebFetch を止めずに、危険な取得だけを検知する PreToolUse フック。

WebFetch 経由で情報が外へ出るとすれば、経路は URL しかない
(WebFetch は GET でページを取得するだけで、本文を送る手段を持たない)。
そこでこのフックは URL だけを検査し、

  - 情報を URL に埋め込んだ形跡があるもの
  - 接続先を隠しているもの

を止める。それ以外は既定で素通しする。つまり「原則禁止・例外許可」ではなく
「原則許可・異常のみ遮断」という運用のための部品。

モード (環境変数 WEBFETCH_GUARD_MODE):
  detect       既定。異常パターンのみ遮断し、他はすべて許可
  ask-unknown  上記に加え、許可リストに無いドメインは利用者に確認させる
  allowlist    上記に加え、許可リストに無いドメインは遮断する

許可リスト: ~/.claude/webfetch-allow.txt (1行1ドメイン、# はコメント)
           先頭が "." の行はサブドメインも含めて一致
監査ログ:   ~/.claude/webfetch-audit.log (JSONL、URL とホストのみ。会話本文は残さない)

settings.json 側の設定:
  {"hooks": {"PreToolUse": [{"matcher": "WebFetch",
    "hooks": [{"type": "command", "command": "/path/to/webfetch_guard.py"}]}]}}
"""

import datetime as dt
import json
import os
import pathlib
import re
import sys
from urllib.parse import urlparse, parse_qsl

ALLOW_FILE = pathlib.Path.home() / ".claude" / "webfetch-allow.txt"
AUDIT_LOG = pathlib.Path.home() / ".claude" / "webfetch-audit.log"

MODE = os.environ.get("WEBFETCH_GUARD_MODE", "detect")

# 情報を詰め込んだ形跡とみなす閾値。通常のドキュメント URL はここまで長くならない。
MAX_URL_LEN = 1000
MAX_PARAM_LEN = 200
BLOB_RE = re.compile(r"[A-Za-z0-9+/=_-]{120,}")   # 長い符号化文字列
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# 接続先が URL から読み取れなくなるサービス。遮断はせず確認に回す。
SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "is.gd", "buff.ly", "rebrand.ly", "cutt.ly", "s.id",
}


def load_allowlist():
    if not ALLOW_FILE.exists():
        return []
    out = []
    for line in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            out.append(line)
    return out


def host_allowed(host, allowlist):
    host = host.lower()
    for entry in allowlist:
        if entry.startswith("."):
            if host == entry[1:] or host.endswith(entry):
                return True
        elif host == entry:
            return True
    return False


def inspect(url):
    """(decision, reason) を返す。decision は allow / ask / deny。"""
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # --- 接続先を偽装・秘匿しているもの ---
    if parsed.username or parsed.password:
        return "deny", "URL に認証情報が埋め込まれています"
    if parsed.scheme not in ("http", "https"):
        return "deny", f"想定外のスキームです: {parsed.scheme or '(なし)'}"
    if IPV4_RE.match(host):
        return "ask", "ドメイン名ではなく IP アドレス宛の取得です"
    if host.lower() in SHORTENERS:
        return "ask", "短縮 URL のため接続先が判別できません"

    # --- 情報を URL に載せている形跡 ---
    if len(url) > MAX_URL_LEN:
        return "deny", f"URL が異常に長い ({len(url)} 文字) ため、データ送信の疑いがあります"
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if len(value) > MAX_PARAM_LEN:
            return "deny", f"クエリ {key} の値が {len(value)} 文字あり、データ送信の疑いがあります"
    blob = BLOB_RE.search(parsed.query or "")
    if blob:
        return "deny", f"符号化されたデータらしき文字列 ({len(blob.group())} 文字) が URL に含まれます"

    return "allow", ""


def audit(url, host, decision, reason):
    """接続先と判定だけを記録する。プロンプトや取得内容は書かない。"""
    entry = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": host,
        "url": url,
        "decision": decision,
        "reason": reason,
        "mode": MODE,
    }
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 記録に失敗しても取得の可否判断は妨げない


def emit(decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # 解釈できない入力で業務を止めない

    if payload.get("tool_name") != "WebFetch":
        sys.exit(0)

    url = (payload.get("tool_input") or {}).get("url", "")
    if not url:
        sys.exit(0)

    host = urlparse(url).hostname or "(不明)"
    decision, reason = inspect(url)

    # 異常が無かった場合のみ、モードに応じた追加判定を行う。
    if decision == "allow" and MODE in ("ask-unknown", "allowlist"):
        if not host_allowed(host, load_allowlist()):
            if MODE == "ask-unknown":
                decision, reason = "ask", f"{host} は許可リストにありません"
            else:
                decision, reason = "deny", f"{host} は許可リストにありません"

    audit(url, host, decision, reason)

    if decision == "deny":
        emit("deny", reason)
        sys.exit(2)          # 終了コード 2 で取得を中止させる
    if decision == "ask":
        emit("ask", reason)
        sys.exit(0)
    sys.exit(0)              # 判定を返さず、通常の権限フローに委ねる


if __name__ == "__main__":
    main()
