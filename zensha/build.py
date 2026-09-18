#!/usr/bin/env python3
"""
build.py — 全社着地モニターの自己完結HTMLを生成する。

  zensha/template.html   … ページ本体（/*__LOGIC__*/ と /*__DATA__*/ を持つ）
  zensha/logic.js        … 計算とルール文生成（そのままインライン注入）
  zensha/data/latest.json… 毎朝のシート抽出（extract.py の出力）
  zensha/data/manual.json… 人手で保守する部分
        ↓
  zensha/out/zensha.html

使い方:
  python3 zensha/build.py
  python3 zensha/build.py --data zensha/testdata/fixture-original-2026-09-08.json -o /tmp/x.html

python3 標準ライブラリのみを使用する。
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

PH_LOGIC = "/*__LOGIC__*/"
PH_DATA = "/*__DATA__*/"

# latest.json に最低限必要なキー（logic.js の REQUIRED_LATEST と同じ）
REQUIRED_LATEST = [
    "basis_date", "data_through", "target_month",
    "days_in_month", "elapsed_days", "remaining_days",
    "fy", "actual_until_index", "revenue", "lks", "sources",
]
REQUIRED_REVENUE_ACCT = ["fin", "mgmt"]
REQUIRED_REVENUE_KEYS = ["total", "lks", "mny", "pro", "hjn", "grs"]
REQUIRED_LKS = ["targets", "online", "kyoten"]
REQUIRED_LANE = ["yomi", "act", "daily", "step"]

# <script> の中に置いても安全な形へ。JSON 値としても JS リテラルとしても正しいまま。
_ESCAPES = (
    ("</", "<\\/"),          # </script> でパーサを閉じさせない
    ("<!--", "<\\!--"),      # コメント開始も潰す
    (" ", "\\u2028"),   # JS では行終端子扱いになる
    (" ", "\\u2029"),
)


def js_json(obj) -> str:
    """JS の <script> に安全に埋め込める JSON リテラル文字列。"""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    for a, b in _ESCAPES:
        s = s.replace(a, b)
    return s


def die(msg: str) -> "None":
    print("エラー: " + msg, file=sys.stderr)
    sys.exit(1)


def read_text(path: str, label: str) -> str:
    if not os.path.isfile(path):
        die("%s が見つかりません: %s" % (label, path))
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_json(path: str, label: str) -> dict:
    raw = read_text(path, label)
    try:
        data = json.loads(raw)
    except ValueError as e:
        die("%s のJSONを読めません: %s: %s" % (label, path, e))
    if not isinstance(data, dict):
        die("%s はオブジェクトである必要があります: %s" % (label, path))
    return data


def check_latest(d: dict, path: str) -> None:
    missing = [k for k in REQUIRED_LATEST if d.get(k) in (None, "")]
    if missing:
        die("latest.json に必須キーがありません: %s（%s）" % ("、".join(missing), path))

    fy = d.get("fy") or {}
    if not isinstance(fy.get("months"), list) or not fy["months"]:
        die("latest.json の fy.months が空です（%s）" % path)
    if d["target_month"] not in fy["months"]:
        die("latest.json の target_month %s が fy.months に含まれていません（%s）"
            % (d["target_month"], path))
    if not isinstance(d.get("actual_until_index"), int) or not (1 <= d["actual_until_index"] <= len(fy["months"])):
        die("latest.json の actual_until_index が 1〜%d の整数ではありません（%s）"
            % (len(fy["months"]), path))

    rev = d.get("revenue") or {}
    n = len(fy["months"])
    for acct in REQUIRED_REVENUE_ACCT:
        if acct not in rev:
            die("latest.json の revenue.%s がありません（%s）" % (acct, path))
        for key in REQUIRED_REVENUE_KEYS:
            series = (rev[acct] or {}).get(key)
            if not isinstance(series, dict):
                die("latest.json の revenue.%s.%s がありません（%s）" % (acct, key, path))
            for field in ("plan", "act"):
                arr = series.get(field)
                if not isinstance(arr, list) or len(arr) != n:
                    die("latest.json の revenue.%s.%s.%s は長さ%dの配列である必要があります（%s）"
                        % (acct, key, field, n, path))
                for v in arr:
                    if not isinstance(v, (int, float)) or isinstance(v, bool):
                        die("latest.json の revenue.%s.%s.%s に数値でない値があります（%s）"
                            % (acct, key, field, path))

    lks = d.get("lks") or {}
    for k in REQUIRED_LKS:
        if k not in lks:
            die("latest.json の lks.%s がありません（%s）" % (k, path))
    targets = lks.get("targets") or {}
    for k in ("online", "kyoten"):
        if not isinstance(targets.get(k), (int, float)) or isinstance(targets.get(k), bool):
            die("latest.json の lks.targets.%s が数値ではありません（%s）" % (k, path))
        if targets[k] <= 0:
            die("latest.json の lks.targets.%s は正の数である必要があります（今は %s・%s）"
                % (k, targets[k], path))
        lane = lks.get(k) or {}
        for f in REQUIRED_LANE:
            if lane.get(f) is None:
                die("latest.json の lks.%s.%s がありません（%s）" % (k, f, path))
        step = lane.get("step") or []
        keys = [r for r in step if r.get("key")]
        if len(keys) != 1:
            die("latest.json の lks.%s.step の key 行はちょうど1つ必要です（今は%d件・%s）"
                % (k, len(keys), path))
        v = (lane.get("daily") or {}).get("v")
        if not isinstance(v, list):
            die("latest.json の lks.%s.daily.v が配列ではありません（%s）" % (k, path))

    if not isinstance(d.get("sources"), list) or not d["sources"]:
        die("latest.json の sources が空です（%s）" % path)

    # 契約の不変条件6（日次の長さ）とカレンダーの整合
    for k in ("online", "kyoten"):
        v = ((lks.get(k) or {}).get("daily") or {}).get("v") or []
        if len(v) != d["elapsed_days"]:
            die("latest.json の lks.%s.daily.v の長さ %d が elapsed_days %s と一致しません（%s）"
                % (k, len(v), d["elapsed_days"], path))
    if d["elapsed_days"] + d["remaining_days"] != d["days_in_month"]:
        die("latest.json の elapsed_days %s ＋ remaining_days %s が days_in_month %s になりません（%s）"
            % (d["elapsed_days"], d["remaining_days"], d["days_in_month"], path))


MANUAL_STALE_DAYS = 7


def manual_stale_days(updated_at, basis_date):
    """basis_date − updated_at の日数。どちらか欠けたら None。"""
    import datetime
    try:
        a = datetime.date.fromisoformat(str(basis_date))
        b = datetime.date.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return None
    return (a - b).days


def overdue_decisions(manual: dict, basis_date):
    """期限（実日付）が basis_date を過ぎている判断事項。due が無いものは対象外。"""
    import datetime
    try:
        b = datetime.date.fromisoformat(str(basis_date))
    except (TypeError, ValueError):
        return []
    out = []
    for d in manual.get("decisions") or []:
        due = d.get("due")
        if not due:
            continue
        try:
            dd = datetime.date.fromisoformat(str(due))
        except (TypeError, ValueError):
            print("警告: manual.json の decisions[].due が日付形式ではありません: %r"
                  % (due,), file=sys.stderr)
            continue
        if dd < b:
            out.append((due, d.get("title", ""), (b - dd).days))
    return out


def check_manual(m: dict, path: str) -> None:
    for k in ("decisions", "notes", "updated_at"):
        if m.get(k) in (None, ""):
            die("manual.json に必須キーがありません: %s（%s）" % (k, path))
    if not isinstance(m["decisions"], list):
        die("manual.json の decisions は配列である必要があります（%s）" % path)
    if not isinstance(m["notes"], list):
        die("manual.json の notes は配列である必要があります（%s）" % path)
    for i, d in enumerate(m["decisions"]):
        if not isinstance(d, dict):
            die("manual.json の decisions[%d] がオブジェクトではありません（%s）" % (i, path))
        if d.get("due") is None and not d.get("due_label"):
            die("manual.json の decisions[%d] は due（YYYY-MM-DD）か due_label のどちらかが必要です（%s）"
                % (i, path))
        if d.get("due") is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d["due"])):
            die("manual.json の decisions[%d].due は YYYY-MM-DD で書いてください（今は %r・%s）"
                % (i, d["due"], path))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="全社着地モニターのHTMLを生成する")
    ap.add_argument("--data", default=os.path.join(HERE, "data", "latest.json"),
                    help="latest.json のパス")
    ap.add_argument("--manual", default=os.path.join(HERE, "data", "manual.json"),
                    help="manual.json のパス")
    ap.add_argument("--template", default=os.path.join(HERE, "template.html"),
                    help="template.html のパス")
    ap.add_argument("--logic", default=os.path.join(HERE, "logic.js"),
                    help="logic.js のパス")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "out", "zensha.html"),
                    help="出力先HTML")
    args = ap.parse_args(argv)

    latest = read_json(args.data, "latest.json")
    manual = read_json(args.manual, "manual.json")
    check_latest(latest, args.data)
    check_manual(manual, args.manual)

    tpl = read_text(args.template, "template.html")
    logic = read_text(args.logic, "logic.js")

    if PH_LOGIC not in tpl:
        die("template.html に %s がありません: %s" % (PH_LOGIC, args.template))
    if PH_DATA not in tpl:
        die("template.html に %s がありません: %s" % (PH_DATA, args.template))

    html = tpl.replace(PH_LOGIC, logic)
    html = html.replace(PH_DATA, js_json({"latest": latest, "manual": manual}))

    if PH_LOGIC in html or PH_DATA in html:
        die("プレースホルダが残っています（注入内容に混ざっていないか確認してください）")
    # 注入内容が <script> を閉じていないか。エスケープ後は素の "</script" は1つだけのはず。
    if html.count("</script") != 1:
        die("生成HTMLに未エスケープの </script が %d 箇所あります（注入内容が script を閉じています）"
            % html.count("</script"))

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    # 期限切れの判断事項（非0終了はしない。画面にも「期限超過」と出る）
    for due, title, days in overdue_decisions(manual, latest.get("basis_date")):
        print("警告: 判断事項の期限が %d日超過しています（%s / %s）。manual.json の decisions を見直してください。"
              % (days, due, title), file=sys.stderr)

    # 手入力の鮮度（非0終了はしない。画面にも同じ警告が出る）
    stale = manual_stale_days(manual.get("updated_at"), latest.get("basis_date"))
    if stale is not None and stale >= MANUAL_STALE_DAYS:
        print("警告: manual.json は %d日前の手入力です（updated_at %s / basis_date %s）。"
              "判断事項の前提を確認してください。"
              % (stale, manual.get("updated_at"), latest.get("basis_date")), file=sys.stderr)

    print("  data     : %s" % args.data)
    print("  manual   : %s（最終更新 %s%s）"
          % (args.manual, manual.get("updated_at"),
             "・%d日前" % stale if stale is not None and stale >= MANUAL_STALE_DAYS else ""))
    print("  基準日   : %s（実績 %s まで） / %s 経過%s日・残%s日"
          % (latest["basis_date"], latest["data_through"], latest["target_month"],
             latest["elapsed_days"], latest["remaining_days"]))
    print("  出力     : %s  (%.1f KB)" % (args.out, os.path.getsize(args.out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
