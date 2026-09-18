#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zensha/ingest.py — Google Drive MCP `read_file_content` の結果ファイルから raw/<name>.md を作る

  python3 zensha/ingest.py <path-to-mcp-result> keiei|marke|kyoten [--raw-dir zensha/raw]

受け付ける入力の形:
  1) 先頭に人間向けメッセージ行が何行かあり、その後に {"fileContent": "..."} の JSON が続くファイル
  2) JSON だけのファイル（{"fileContent": ...} / {"content": ...} / {"result": {"fileContent": ...}} など）
  3) JSON が見つからない場合は、ファイル全体を Markdown 本文とみなす（--allow-plain 指定時のみ）

fileContent の中身をそのまま raw/<name>.md に書き出す。

標準ライブラリのみ使用。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

JST = _dt.timezone(_dt.timedelta(hours=9))

NAMES = ("keiei", "marke", "kyoten")

# MCP の結果に title / id が無い場合の既定値（CONTRACT.md の記載）
DEFAULTS = {
    "keiei": {"title": "FY26_経営モニタリング",
              "id": "1oIz45k-Qkr8AY3v4zBQAiYHDxvg21nvVPSP5xX5q9pE",
              "label": "全社（単月確認用・通期着地見通し）"},
    "marke": {"title": "26年9月_マーケジスイ進捗管理表",
              "id": "1a6Aud7H-31gs64lDv2Iyww6DnS98wJcuoOssx6nGhJs"},
    "kyoten": {"title": "FY26_拠点モニタリング",
               "id": "11pv1YWcxOvJwJjhsNFZa9zGoy0GCn3bauNtSymMppcg"},
}

TITLE_KEYS = ("title", "name", "fileName", "filename")
ID_KEYS = ("id", "fileId", "file_id", "documentId")

# fileContent が入り得るキー（上から順に探す）
CONTENT_KEYS = ("fileContent", "file_content", "content", "text", "body", "markdown")


def _find_content(obj, depth=0):
    """辞書/リストを再帰的に辿って、Markdown 本文らしい文字列を返す。"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for k in CONTENT_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v
        for v in obj.values():
            got = _find_content(v, depth + 1)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_content(v, depth + 1)
            if got is not None:
                return got
    return None


def _find_key(obj, keys, depth=0):
    """辞書/リストを再帰的に辿って、指定キーの文字列値を返す。"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            got = _find_key(v, keys, depth + 1)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_key(v, keys, depth + 1)
            if got is not None:
                return got
    return None


def _iter_json_candidates(raw):
    """本文中の '{' で始まる部分文字列を先頭から順に JSON として試す。"""
    dec = json.JSONDecoder()
    start = 0
    while True:
        i = raw.find("{", start)
        if i < 0:
            return
        try:
            obj, end = dec.raw_decode(raw[i:])
        except ValueError:
            start = i + 1
            continue
        yield obj
        start = i + max(end, 1)


def extract_file_content(raw, allow_plain=False):
    """(fileContent, title, id) を返す。title / id は見つからなければ None。"""
    for obj in _iter_json_candidates(raw):
        got = _find_content(obj)
        if got is not None:
            return got, _find_key(obj, TITLE_KEYS), _find_key(obj, ID_KEYS)
    if allow_plain:
        return raw, None, None
    return None, None, None


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="Google Drive MCP の read_file_content 結果から raw/<name>.md を書き出す")
    ap.add_argument("result_path", help="MCP の結果を保存したファイル")
    ap.add_argument("name", choices=NAMES, help="出力先 raw/<name>.md")
    ap.add_argument("--raw-dir", default=os.path.join(here, "raw"))
    ap.add_argument("--allow-plain", action="store_true",
                    help="JSON が見つからないときにファイル全体を Markdown とみなす")
    ap.add_argument("--title", default=None,
                    help="スプレッドシート名。省略時は MCP 結果から検出、それも無ければ既定値。")
    ap.add_argument("--id", dest="file_id", default=None,
                    help="スプレッドシート ID。省略時は MCP 結果から検出、それも無ければ既定値。")
    args = ap.parse_args(argv)

    if not os.path.exists(args.result_path):
        print("エラー: %s がありません。" % args.result_path, file=sys.stderr)
        return 2
    with open(args.result_path, encoding="utf-8") as fh:
        raw = fh.read()

    content, found_title, found_id = extract_file_content(raw, args.allow_plain)
    if content is None:
        print("エラー: %s から fileContent を取り出せませんでした"
              "（JSON が見つかりません。全体を Markdown として扱うなら --allow-plain）。"
              % args.result_path, file=sys.stderr)
        return 1
    if "|" not in content:
        print("エラー: 取り出した内容に Markdown 表（'|'）が含まれていません。"
              "MCP の結果ファイルが正しいか確認してください。", file=sys.stderr)
        return 1

    if not os.path.isdir(args.raw_dir):
        os.makedirs(args.raw_dir)
    out = os.path.join(args.raw_dir, args.name + ".md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(content)
    nblocks = sum(1 for c in content.split("\n\n") if c.strip())

    d = DEFAULTS[args.name]
    title = args.title or found_title or d["title"]
    file_id = args.file_id or found_id or d["id"]
    meta = {
        "title": title,
        "id": file_id,
        "url": "https://docs.google.com/spreadsheets/d/%s/edit" % file_id,
        "ingested_at": _dt.datetime.now(JST).replace(microsecond=0).isoformat(),
        "source_file": os.path.basename(args.result_path),
    }
    if d.get("label"):
        meta["label"] = d["label"]
    meta_path = os.path.join(args.raw_dir, args.name + ".meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("書き出しました: %s（%d 文字 / 約 %d ブロック）" % (out, len(content), nblocks))
    print("メタ情報: %s（title=%s / id=%s%s）"
          % (meta_path, title, file_id,
             "" if (args.title or found_title) else " ※title は既定値"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
