#!/usr/bin/env python3
"""外部接続を「禁止」せず「検査」する PreToolUse フック。WebFetch と Bash の両方を見る。

設計方針:
  実際の利用ログから設計している。止めてよいのは、業務で現れない形のものだけ。
  通常のドキュメント参照、社内 API 呼び出し、git 操作は一切妨げない。

WebFetch と Bash では危険度が異なるため、検査項目を変えている。

  WebFetch : GET でページを取得するだけで本文を送れない。
             よって流出経路は URL のみ。URL だけを見ればよい。
  Bash     : curl などは本文を POST でき、ファイルもアップロードできる。
             URL に加えて「ローカルのファイルを送ろうとしていないか」を見る。

モード (環境変数 NET_GUARD_MODE):
  detect       既定。異常パターンのみ遮断し、他はすべて許可
  ask-unknown  上記に加え、許可リストに無いドメインは利用者に確認させる
  allowlist    上記に加え、許可リストに無いドメインは遮断する

許可リスト: ~/.claude/net-allow.txt (1行1ドメイン、# はコメント、先頭 "." でサブドメイン含む)
監査ログ:   ~/.claude/net-audit.log (JSONL。接続先と判定のみ。会話本文もコマンド全文も残さない)
"""

import datetime as dt
import json
import os
import pathlib
import re
import sys
from urllib.parse import urlparse, parse_qsl

ALLOW_FILE = pathlib.Path.home() / ".claude" / "net-allow.txt"
AUDIT_LOG = pathlib.Path.home() / ".claude" / "net-audit.log"

NO_NETWORK = "(ネットワーク接続なし)"
MODE = os.environ.get("NET_GUARD_MODE", "detect")

MAX_URL_LEN = 1000
MAX_PARAM_LEN = 200
URL_RE = re.compile(r"""https?://[^\s"'`)\]}<>|;]+""")
BLOB_RE = re.compile(r"[A-Za-z0-9+/=_-]{120,}")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "is.gd", "buff.ly", "rebrand.ly", "cutt.ly", "s.id",
}

# curl / scp などでローカルのファイルを送り出す指定。WebFetch には存在しない経路。
UPLOAD_FLAGS = [
    "--data-binary @", "--data @", "--data-raw @", "--upload-file",
    "-d @", "-T ", "--form",
]
# ファイルの中身をパイプでネットワークコマンドへ渡す形。
PIPE_TO_NET_RE = re.compile(
    r"(cat|tail|head|base64|gzip|tar|openssl|env|printenv)\b[^|]*\|\s*(curl|wget|nc|ncat|socat)\b"
)
# 実際にネットワークへ出るコマンドが、コマンドとして呼ばれている箇所。
NET_CMD_RE = re.compile(r"(?:^|[|;&(]|\s)(curl|wget|nc|ncat|socat|scp|rsync)\s")
# ヒアドキュメントの本文。スクリプトを書き出すだけの操作を通信と誤認しないため除去する。
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1\n.*?\n\2\s*$",
                        re.DOTALL | re.MULTILINE)


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


def inspect_url(url):
    """URL 単体の検査。WebFetch と Bash に共通。"""
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if parsed.username or parsed.password:
        return "deny", "URL に認証情報が埋め込まれています"
    if parsed.scheme not in ("http", "https"):
        return "deny", f"想定外のスキームです: {parsed.scheme or '(なし)'}"
    if IPV4_RE.match(host):
        return "ask", "ドメイン名ではなく IP アドレス宛の接続です"
    if host.lower() in SHORTENERS:
        return "ask", "短縮 URL のため接続先が判別できません"
    if len(url) > MAX_URL_LEN:
        return "deny", f"URL が異常に長い ({len(url)} 文字) ため、データ送信の疑いがあります"
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if len(value) > MAX_PARAM_LEN:
            return "deny", f"クエリ {key} の値が {len(value)} 文字あり、データ送信の疑いがあります"
    blob = BLOB_RE.search(parsed.query or "")
    if blob:
        return "deny", f"符号化されたデータらしき文字列 ({len(blob.group())} 文字) が URL に含まれます"
    return "allow", ""


def strip_heredocs(cmd):
    """ファイル生成のためのヒアドキュメント本文を取り除く。

    スクリプトや設定ファイルを書き出すコマンドは、本文に URL や curl の文字列を
    含むことが多い。それを通信とみなすと通常の作業が止まるため、先に落とす。
    """
    return HEREDOC_RE.sub("\n", cmd)


def inspect_command(cmd):
    """Bash コマンドの検査。ネットワークへ出るコマンドが無ければ何も言わない。"""
    body = strip_heredocs(cmd)
    if not NET_CMD_RE.search(body):
        return "allow", "", NO_NETWORK

    urls = URL_RE.findall(body)
    host = urlparse(urls[0]).hostname or "(不明)" if urls else "(不明)"

    # ローカルの内容を送出しようとしていれば、接続先を問わず確認に回す。
    if PIPE_TO_NET_RE.search(body):
        return "ask", "ローカルの内容をネットワークコマンドへ渡そうとしています", host
    for flag in UPLOAD_FLAGS:
        if flag in body:
            return "ask", f"ファイル送信の指定 ({flag.strip()}) が含まれます", host

    for url in urls:
        decision, reason = inspect_url(url)
        if decision != "allow":
            return decision, reason, urlparse(url).hostname or "(不明)"
    return "allow", "", host


def audit(tool, host, decision, reason):
    entry = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tool": tool, "host": host,
        "decision": decision, "reason": reason, "mode": MODE,
    }
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


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
        sys.exit(0)

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool == "WebFetch":
        url = tool_input.get("url", "")
        if not url:
            sys.exit(0)
        host = urlparse(url).hostname or "(不明)"
        decision, reason = inspect_url(url)
    elif tool == "Bash":
        cmd = tool_input.get("command", "")
        decision, reason, host = inspect_command(cmd)
        if host == NO_NETWORK:
            sys.exit(0)   # ローカル作業には一切干渉しない
    else:
        sys.exit(0)

    if decision == "allow" and MODE in ("ask-unknown", "allowlist"):
        if not host_allowed(host, load_allowlist()):
            decision = "ask" if MODE == "ask-unknown" else "deny"
            reason = f"{host} は許可リストにありません"

    audit(tool, host, decision, reason)

    if decision == "deny":
        emit("deny", reason)
        sys.exit(2)
    if decision == "ask":
        emit("ask", reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
