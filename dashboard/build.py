#!/usr/bin/env python3
"""
build.py — 財務売上着地モニターの自己完結HTMLを生成する。

  dashboard/template.html   … ページ本体（プレースホルダ /*__LOGIC__*/ と /*__DATA__*/ を持つ）
  dashboard/logic.js        … 計算ロジック（そのままインライン注入）
  dashboard/data/latest.json… スナップショット（無ければ --fixture のパスを使う）
  dashboard/sql/q*.sql      … 6本のクエリ（ライブ更新用に埋め込む・無ければ null）
        ↓
  dashboard/out/dashboard.html   ← Claude Artifact としてそのまま公開できる断片

使い方:
  python3 dashboard/build.py
  python3 dashboard/build.py --fixture /path/to/fixture.json
  python3 dashboard/build.py --standalone -o /tmp/preview.html   # ローカル閲覧用にラップ

python3 標準ライブラリのみを使用する。
"""

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser

QUERY_KEYS = [
    "q1_official_monthly",
    "q2_lks_pending",
    "q3_mny_pd_pending",
    "q4_lks_channel_booked",
    "q5_conv_profile",
    "q6_conv_actuals",
]

PH_LOGIC = "/*__LOGIC__*/"
PH_DATA = "/*__DATA__*/"

HERE = os.path.dirname(os.path.abspath(__file__))

# <script> の中に置いても安全な形へ。JSON 値としても JS リテラルとしても正しいまま。
_ESCAPES = (
    ("</", "<\\/"),          # </script> でパーサを閉じさせない
    ("<!--", "<\\!--"),      # コメント開始も潰す
    (" ", "\\u2028"),   # JS では行終端子扱いになる
    (" ", "\\u2029"),
)

# 自己終了しない/閉じタグ不要の HTML 要素
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def js_json(obj) -> str:
    """JS の <script> に安全に埋め込める JSON リテラル文字列。"""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    for a, b in _ESCAPES:
        s = s.replace(a, b)
    return s


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_snapshot(args, warn) -> dict:
    """latest.json を読む。無ければ fixture。どちらも無ければ空の骨格を返す。"""
    for label, path in (("data", args.data), ("fixture", args.fixture)):
        if not path:
            continue
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError) as e:
                warn(f"{label} の読み込みに失敗: {path}: {e}")
                continue
            data.setdefault("queries", {})
            for k in QUERY_KEYS:
                q = data["queries"].get(k)
                if not isinstance(q, dict) or not isinstance(q.get("rows"), list):
                    warn(f"{label} に {k}.rows が無い（空行として扱う）")
                    data["queries"][k] = {"rows": []}
            data.setdefault("meta", {})
            if label == "fixture":
                data["meta"]["fixture"] = True
                data["meta"].setdefault("source", f"fixture: {os.path.basename(path)}")
            print(f"  snapshot : {path}"
                  f"  ({', '.join(k[:2] + ':' + str(len(data['queries'][k]['rows'])) for k in QUERY_KEYS)})")
            return data
        warn(f"{label} が見つからない: {path}")

    warn("スナップショットが一つも無いため空データで生成する")
    return {
        "generated_at": "",
        "basis_date": "",
        "target_month": "",
        "queries": {k: {"rows": []} for k in QUERY_KEYS},
        "meta": {"empty": True},
    }


def load_queries(sql_dir: str, warn) -> dict:
    """dashboard/sql/*.sql を読む。見つからないキーは None（ページ側で「SQL 未配置」表示）。"""
    out = {k: None for k in QUERY_KEYS}
    if not os.path.isdir(sql_dir):
        warn(f"SQL ディレクトリが無い: {sql_dir}（ライブ更新は無効で生成する）")
        return out

    files = sorted(f for f in os.listdir(sql_dir) if f.endswith(".sql"))
    stems = {os.path.splitext(f)[0]: os.path.join(sql_dir, f) for f in files}

    for key in QUERY_KEYS:
        path = stems.get(key)
        if path is None:
            prefix = key.split("_", 1)[0] + "_"     # q1_ / q2_ ...
            cands = [p for s, p in sorted(stems.items()) if s.startswith(prefix)]
            if len(cands) == 1:
                path = cands[0]
            elif len(cands) > 1:
                path = cands[0]
                warn(f"{key}: 前方一致が複数（{[os.path.basename(c) for c in cands]}）→ "
                     f"{os.path.basename(path)} を使う")
        if path is None:
            warn(f"{key}: SQL が見つからない（{sql_dir}）")
            continue
        try:
            sql = read_text(path).strip()
        except OSError as e:
            warn(f"{key}: 読み込み失敗 {path}: {e}")
            continue
        if not sql:
            warn(f"{key}: SQL が空 {path}")
            continue
        if ";" in sql.rstrip().rstrip(";"):
            warn(f"{key}: 文の区切り ';' が途中にある（単一SELECT文であること）")
        out[key] = sql
    found = sum(1 for v in out.values() if v)
    print(f"  sql      : {found}/{len(QUERY_KEYS)} 本 ({sql_dir})")
    return out


class TagBalance(HTMLParser):
    """出力の素朴な構文サニティ: 閉じ忘れ・余分な閉じタグを見る。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                unclosed = self.stack[i + 1:]
                if unclosed:
                    self.errors.append(
                        f"</{tag}> の内側で閉じられていない: "
                        + ", ".join(f"<{t}>(L{ln})" for t, ln in unclosed))
                del self.stack[i:]
                return
        self.errors.append(f"対応する開始タグの無い </{tag}>")


def sanity(html: str, out_path: str) -> int:
    problems = []

    for ph in (PH_LOGIC, PH_DATA):
        if ph in html:
            problems.append(f"プレースホルダ {ph} が残っている")
    if "window.__APP__" not in html:
        problems.append("window.__APP__ が埋め込まれていない")
    if "FALogic" not in html:
        problems.append("logic.js が注入されていない")
    if "<title>" not in html[:4096]:
        problems.append("<title> がファイル先頭 4KB に無い")

    # <script> の中身に生の </script> が無いこと
    for m in re.finditer(r"<script\b[^>]*>", html, re.I):
        end = html.find("</script>", m.end())
        if end < 0:
            problems.append(f"閉じられていない <script>（offset {m.start()}）")
            break
        body = html[m.end():end]
        if re.search(r"</\s*script", body, re.I):
            problems.append("script 内に生の </script> がある")

    # 外部ホストは Google Fonts のみ
    for m in re.finditer(r'(?:href|src)\s*=\s*"(https?://[^"]+)"', html, re.I):
        host = m.group(1).split("/")[2].lower()
        if host not in ("fonts.googleapis.com", "fonts.gstatic.com"):
            problems.append(f"外部ホストへの参照: {host}")

    tb = TagBalance()
    tb.feed(html)
    tb.close()
    problems.extend(tb.errors)
    if tb.stack:
        problems.append("閉じられていないタグ: "
                        + ", ".join(f"<{t}>(L{ln})" for t, ln in tb.stack))

    # テーマ規則: media/[data-theme] ブロック内にしか定義が無い色トークンが無いか
    problems.extend(theme_scan(html))

    if problems:
        print("\n  ! サニティチェックで問題:", file=sys.stderr)
        for p in problems:
            print("    - " + p, file=sys.stderr)
        return 1
    print(f"  sanity   : OK（{len(html):,} bytes）")
    print(f"  out      : {out_path}")
    return 0


def theme_scan(html: str):
    """bare :root に定義が無く dark ブロックにしか無いトークンを検出する。"""
    styles = re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.I | re.S)
    css = "\n".join(styles)
    if not css:
        return []

    def props(block: str):
        return set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", block))

    # bare :root { ... }（media / [data-theme] を含まない先頭ブロック）
    base = set()
    m = re.search(r"(?<![\w\]\)-]):root\s*\{(.*?)\}", css, re.S)
    if m:
        base = props(m.group(1))

    dark = set()
    for pat in (r':root:not\(\[data-theme="light"\]\)\s*\{(.*?)\}',
                r':root\[data-theme="dark"\]\s*\{(.*?)\}'):
        for mm in re.finditer(pat, css, re.S):
            dark |= props(mm.group(1))

    missing = sorted(dark - base)
    used = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", css))
    undefined = sorted(used - base - dark)
    out = []
    if missing:
        out.append("dark ブロックにしか定義が無いトークン: " + ", ".join(missing))
    if undefined:
        out.append("どこにも定義が無い var(): " + ", ".join(undefined))
    return out


STANDALONE_HEAD = (
    "<!doctype html>\n<html lang=\"ja\">\n<head>\n<meta charset=\"utf-8\">\n"
)
STANDALONE_TAIL = "\n</body>\n</html>\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="財務売上着地モニターのHTMLを生成する")
    ap.add_argument("--template", default=os.path.join(HERE, "template.html"))
    ap.add_argument("--logic", default=os.path.join(HERE, "logic.js"))
    ap.add_argument("--data", default=os.path.join(HERE, "data", "latest.json"),
                    help="スナップショット。無ければ --fixture を使う")
    ap.add_argument("--fixture", default=os.path.join(HERE, "fixture.json"),
                    help="latest.json が無いときに使うフィクスチャ")
    ap.add_argument("--sql", default=os.path.join(HERE, "sql"))
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "out", "dashboard.html"))
    ap.add_argument("--standalone", action="store_true",
                    help="doctype/html/head/body で包む（ローカル閲覧用。Artifact 公開には不要）")
    ap.add_argument("--strict", action="store_true", help="警告が1件でもあれば失敗させる")
    args = ap.parse_args()

    warnings = []

    def warn(msg):
        warnings.append(msg)
        print("  ! " + msg, file=sys.stderr)

    print("build dashboard")
    for path in (args.template, args.logic):
        if not os.path.isfile(path):
            print(f"  ! 必須ファイルが無い: {path}", file=sys.stderr)
            return 2

    template = read_text(args.template)
    logic = read_text(args.logic)
    if PH_LOGIC not in template or PH_DATA not in template:
        print(f"  ! template に {PH_LOGIC} / {PH_DATA} が無い", file=sys.stderr)
        return 2
    for a, b in _ESCAPES:
        if a in logic:
            logic = logic.replace(a, b)
    print(f"  logic    : {args.logic} ({len(logic):,} bytes)")

    data = load_snapshot(args, warn)
    queries = load_queries(args.sql, warn)

    app_literal = "window.__APP__={data:" + js_json(data) + ",queries:" + js_json(queries) + "};"

    html = template.replace(PH_LOGIC, logic).replace(PH_DATA, app_literal)
    if args.standalone:
        title = "財務売上着地モニター"
        html = STANDALONE_HEAD + f"<title>{title}</title>\n</head>\n<body>\n" + html + STANDALONE_TAIL

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    rc = sanity(html, args.out)
    if warnings:
        print(f"  warnings : {len(warnings)} 件")
        if args.strict:
            return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
