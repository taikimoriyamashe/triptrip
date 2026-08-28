#!/usr/bin/env python3
"""Claude Code のローカルセッション履歴から「外部への接続実績」を抽出する。

Claude Code は会話を ~/.claude/projects/<project>/<session>.jsonl に保存している
(既定の保持期間は 30 日)。このスクリプトはそのファイルを読み、

    いつ / どのセッションで / どのツールを使い / どこへ接続したか

だけを出力する。プロンプト本文・アシスタントの応答・ツールの出力は一切出力しない。

使い方:
    python3 claude_external_access_report.py              # 一覧 (TSV)
    python3 claude_external_access_report.py --summary    # 集計のみ
    python3 claude_external_access_report.py --days 14    # 直近14日
    python3 claude_external_access_report.py --tool WebFetch
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
from collections import Counter
from urllib.parse import urlparse

# Bash コマンド中に現れる URL。ホスト名だけを取り出す用途に使う。
URL_RE = re.compile(r"""https?://[^\s"'`)\]}<>|;]+""")

# 外部接続を伴わないローカル専用ツール。件数の把握には出すが接続先は無い。
LOCAL_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit", "TodoWrite"}


def host_of(url):
    try:
        h = urlparse(url).netloc
    except ValueError:
        return "(解析不能)"
    return h or "(解析不能)"


def classify(name, tool_input, include_queries):
    """ツール呼び出しを (種別, 接続先, ホスト) に変換する。外部接続でなければ None。"""
    if name == "WebFetch":
        url = tool_input.get("url", "")
        return ("WebFetch", url, host_of(url))

    if name == "WebSearch":
        # 検索語は業務内容が読み取れるため、既定では出さない。
        q = tool_input.get("query", "")
        target = q if include_queries else "(検索語は非出力)"
        return ("WebSearch", target, "(検索エンジン)")

    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else "(不明)"
        return ("MCP", name, server)

    if name == "Bash":
        # Bash からの外部通信 (curl / wget など) は WebFetch の制限では止まらないため、
        # コマンド文字列から URL を拾ってホストだけを記録する。
        cmd = tool_input.get("command", "")
        urls = URL_RE.findall(cmd)
        if urls:
            return ("Bash(network)", urls[0], host_of(urls[0]))
        return None

    return None


def iter_tool_uses(path):
    """1 つの履歴ファイルから tool_use を順に取り出す。"""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    yield rec, block


def parse_ts(raw):
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=os.path.expanduser("~/.claude/projects"),
                    help="履歴ディレクトリ (既定: ~/.claude/projects)")
    ap.add_argument("--days", type=int, default=30, help="直近 N 日に絞る (既定: 30)")
    ap.add_argument("--tool", help="種別で絞る (WebFetch / WebSearch / MCP / Bash(network))")
    ap.add_argument("--summary", action="store_true", help="明細を出さず集計だけ表示")
    ap.add_argument("--include-queries", action="store_true",
                    help="WebSearch の検索語も出力する (既定は非出力)")
    ap.add_argument("--all-tools", action="store_true",
                    help="外部接続以外のツールも件数に含める")
    args = ap.parse_args()

    root = pathlib.Path(args.dir)
    if not root.exists():
        sys.exit(f"履歴が見つかりません: {root}\n"
                 "Claude Code をこの端末で使っていないか、保持期間を過ぎています。")

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    files = sorted(root.rglob("*.jsonl"))

    rows = []
    all_tool_counts = Counter()
    oldest = newest = None

    for path in files:
        for rec, block in iter_tool_uses(path):
            ts = parse_ts(rec.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
            oldest = ts if oldest is None or ts < oldest else oldest
            newest = ts if newest is None or ts > newest else newest

            name = block.get("name", "")
            all_tool_counts[name] += 1

            hit = classify(name, block.get("input") or {}, args.include_queries)
            if hit is None:
                continue
            kind, target, host = hit
            if args.tool and kind != args.tool:
                continue
            rows.append({
                "timestamp": ts.isoformat(),
                "session": (rec.get("sessionId") or "")[:8],
                "project": rec.get("cwd") or "",
                "kind": kind,
                "host": host,
                "target": target,
            })

    rows.sort(key=lambda r: r["timestamp"])

    if not args.summary:
        print("timestamp\tsession\tkind\thost\ttarget\tproject")
        for r in rows:
            print("\t".join([r["timestamp"], r["session"], r["kind"],
                             r["host"], r["target"], r["project"]]))
        print(file=sys.stderr)

    span = "データなし"
    if oldest and newest:
        span = f"{oldest.date()} 〜 {newest.date()}"

    out = sys.stderr if not args.summary else sys.stdout
    print(f"■ 対象: {len(files)} セッション / 直近 {args.days} 日 / 記録範囲 {span}", file=out)
    print(f"■ 外部接続を伴うツール呼び出し: {len(rows)} 件", file=out)

    if rows:
        print("\n[種別ごとの件数]", file=out)
        for kind, n in Counter(r["kind"] for r in rows).most_common():
            print(f"  {n:6d}  {kind}", file=out)

        print("\n[接続先ごとの件数]", file=out)
        for host, n in Counter(r["host"] for r in rows).most_common():
            print(f"  {n:6d}  {host}", file=out)

    if args.all_tools:
        print("\n[全ツールの呼び出し回数]", file=out)
        for name, n in all_tool_counts.most_common():
            mark = "  (ローカル)" if name in LOCAL_TOOLS else ""
            print(f"  {n:6d}  {name}{mark}", file=out)


if __name__ == "__main__":
    main()
