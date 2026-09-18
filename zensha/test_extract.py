#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zensha/test_extract.py — latest.json と extract.py の自己検査

  python3 zensha/test_extract.py [--json zensha/data/latest.json]

検査内容
  (a) CONTRACT.md の不変条件 1〜8
  (b) 原本 REV_ALL の期初計画（plan）12ヶ月 × fin/mgmt × 6キー との厳密一致
  (c) 実績確定月までの act が原本と ±0.5% 以内
      （それを超えるセルは「シート側で実績が改訂された既知の差」リストにあれば WARN 扱い）
  (d) STEP の p が原本と一致（シートに当該値が残っていない項目は WARN 扱い）
  (e) パーサ単体テスト（数値表記の正規化 / Markdown ブロック分割 / 日付・年月）

全て PASS なら exit 0、1件でも FAIL なら非0終了。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extract as E  # noqa: E402

JST = _dt.timezone(_dt.timedelta(hours=9))

SERVICE_KEYS = ["total", "lks", "mny", "pro", "hjn", "grs"]
COMPONENT_KEYS = ["lks", "mny", "pro", "hjn", "grs"]

# ---------------------------------------------------------------------------
# (c) の既知差分: 2026-09-08 の原本取得後にシート側で改訂された実績セル。
#     「パーサのバグ」ではなく「シートの値そのものが変わった」ことを人が確認済み。
#     ここに載っているセルは ±0.5% を超えても WARN（PASS 扱い）にする。
#     ※ 値は固定しない（今後さらに改訂されても壊れないように）。
KNOWN_ACT_REVISIONS = {
    ("mgmt", "mny", 2): "管理会計 SHEmoney 2026-06 実績がシートで改訂された",
    ("mgmt", "hjn", 0): "管理会計 法人 2026-04 実績がシートで改訂された（通期着地ワイド表には旧値が残っている）",
    ("mgmt", "hjn", 2): "管理会計 法人 2026-06 実績がシートで改訂された",
    ("mgmt", "hjn", 4): "管理会計 法人 2026-08 実績がシートで改訂された",
    ("mgmt", "grs", 4): "管理会計 グロースタジオ 2026-08 実績がシートで改訂された",
}

# ---------------------------------------------------------------------------
# (d) 原本 STEP の p。シートに当該値が残っていない項目は WARN。
ORIG_STEP_P = {
    "online": {"申込": 7612, "予約": 7599, "参加": 3135, "成約": 868,
               "参加率": 41.3, "成約率": 27.7, "CPA": 23022},
    "kyoten": {"申込": 824, "参加": 399, "成約": 148,
               "参加率": 48.4, "成約率": 37.1, "CPA": 32154},
}
# 原本の値が現在のシートから消えている（＝シート側で目標が引き直された）項目
KNOWN_STEP_P_CHANGES = {
    ("online", "参加"): "marke「ALLユーザ」目標の体験レッスン参加数が 3,135 → 3,420 に改訂された",
    ("online", "参加率"): "同上（参加率目標が 41.3% → 45.0% に改訂された）",
    ("online", "成約率"): "成約p(868) / 参加p の算出値。参加p の改訂に伴って 27.7% → 25.4% になった",
}


class Runner(object):
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warned = 0

    def ok(self, name):
        self.passed += 1
        print("PASS  %s" % name)

    def ng(self, name, detail=""):
        self.failed += 1
        print("FAIL  %s%s" % (name, ("\n        " + detail) if detail else ""))

    def warn(self, name, detail=""):
        self.warned += 1
        print("WARN  %s%s" % (name, ("\n        " + detail) if detail else ""))

    def check(self, cond, name, detail=""):
        if cond:
            self.ok(name)
        else:
            self.ng(name, detail)
        return cond


# ---------------------------------------------------------------------------
# 原本 HTML から REV_ALL / STEP を取り出す
# ---------------------------------------------------------------------------

def load_original_rev(html_path):
    with open(html_path, encoding="utf-8") as fh:
        html = fh.read()
    s = html.index("var REV_ALL")
    e = html.index("var acct", s)
    js = html[s:e]
    js = js[js.index("{"):js.rindex("}") + 1]
    js = re.sub(r"(\w+)\s*:", r'"\1":', js)
    js = re.sub(r",\s*([}\]])", r"\1", js)
    return json.loads(js)


# ---------------------------------------------------------------------------
# (a) 不変条件
# ---------------------------------------------------------------------------

def test_invariants(r, doc):
    months = doc["fy"]["months"]

    # 1
    bad = []
    for acct in ("fin", "mgmt"):
        for key in SERVICE_KEYS:
            for fld in ("plan", "act"):
                arr = doc["revenue"][acct][key][fld]
                if len(arr) != 12:
                    bad.append("%s.%s.%s の長さ=%d" % (acct, key, fld, len(arr)))
                for i, v in enumerate(arr):
                    if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
                        bad.append("%s.%s.%s[%d]=%r" % (acct, key, fld, i, v))
    r.check(not bad, "不変条件1: revenue の各系列は長さ12・全要素が数値", "; ".join(bad[:5]))

    # 2
    bad = []
    for acct in ("fin", "mgmt"):
        for i in range(12):
            tot = doc["revenue"][acct]["total"]["act"][i]
            ssum = sum(doc["revenue"][acct][k]["act"][i] for k in COMPONENT_KEYS)
            if abs(tot - ssum) > 1:
                bad.append("%s[%d]: total=%d Σ=%d" % (acct, i, tot, ssum))
    r.check(not bad, "不変条件2: total.act == Σ5サービス（±1円）", "; ".join(bad[:5]))

    # 3 / 4 は (b)(c) で詳細検査するので、ここでは CONTRACT に明記された代表値のみ
    spot = [
        (("fin", "total", "plan", 0), 382595924),
        (("fin", "lks", "plan", 0), 339439531),
        (("mgmt", "total", "plan", 0), 385856895),
        (("fin", "grs", "plan", 0), 20900000),
    ]
    bad = []
    for (acct, key, fld, i), want in spot:
        got = doc["revenue"][acct][key][fld][i]
        if got != want:
            bad.append("%s.%s.%s[%d]=%d (期待 %d)" % (acct, key, fld, i, got, want))
    r.check(not bad, "不変条件3: CONTRACT 記載の期初計画代表値と一致", "; ".join(bad))

    # 5
    aui = doc["actual_until_index"]
    r.check(isinstance(aui, int) and 1 <= aui <= 12,
            "不変条件5a: actual_until_index が 1..12", "actual_until_index=%r" % aui)
    r.check(doc["target_month"] in months,
            "不変条件5b: target_month が fy.months に含まれる",
            "target_month=%s months=%s" % (doc["target_month"], months))

    # 6
    for lane in ("online", "kyoten"):
        d = doc["lks"][lane]["daily"]
        r.check(len(d["v"]) == doc["elapsed_days"],
                "不変条件6: lks.%s.daily.v の長さ == elapsed_days(%d)" % (lane, doc["elapsed_days"]),
                "長さ=%d" % len(d["v"]))
        r.check(len(d["dates"]) == len(d["dow"]) == len(d["v"]),
                "不変条件6: lks.%s.daily の dates/dow/v の長さが揃う" % lane,
                "dates=%d dow=%d v=%d" % (len(d["dates"]), len(d["dow"]), len(d["v"])))

    # 7
    for lane in ("online", "kyoten"):
        step = doc["lks"][lane]["step"]
        nkey = sum(1 for s in step if s.get("key"))
        r.check(nkey == 1, "不変条件7a: lks.%s.step の key 行がちょうど1つ" % lane, "key行=%d" % nkey)
        bad = [("%s.%s" % (s["n"], f))
               for s in step for f in ("p", "y", "a")
               if not isinstance(s[f], (int, float)) or isinstance(s[f], bool)]
        r.check(not bad, "不変条件7b: lks.%s.step の p/y/a が全て数値" % lane, "; ".join(bad))

    # 8
    today = _dt.datetime.now(JST).date().isoformat()
    r.check(doc["basis_date"] == today,
            "不変条件8: basis_date が JST の今日と一致",
            "basis_date=%s / JST今日=%s" % (doc["basis_date"], today))

    # 契約のその他の整合（日付計算）
    b = _dt.date.fromisoformat(doc["basis_date"])
    dt = _dt.date.fromisoformat(doc["data_through"])
    r.check(dt == b - _dt.timedelta(days=1),
            "追加: data_through == basis_date の前日", "%s / %s" % (doc["data_through"], doc["basis_date"]))
    r.check(doc["remaining_days"] == doc["days_in_month"] - doc["elapsed_days"],
            "追加: remaining_days == days_in_month - elapsed_days")


# ---------------------------------------------------------------------------
# (b) plan 厳密一致
# ---------------------------------------------------------------------------

def test_plan_exact(r, doc, orig):
    bad = []
    for acct in ("fin", "mgmt"):
        for key in SERVICE_KEYS:
            got = doc["revenue"][acct][key]["plan"]
            want = orig[acct][key]["plan"]
            if got != want:
                for i in range(12):
                    if got[i] != want[i]:
                        bad.append("%s.%s.plan[%d]: got=%d orig=%d" % (acct, key, i, got[i], want[i]))
    r.check(not bad,
            "(b) 期初計画 plan が原本 REV_ALL と 12ヶ月 × fin/mgmt × 6キー で厳密一致",
            "\n        ".join(bad[:10]))


# ---------------------------------------------------------------------------
# (c) act ±0.5%
# ---------------------------------------------------------------------------

def test_act_tolerance(r, doc, orig):
    aui = doc["actual_until_index"]
    bad, warned = [], []
    for acct in ("fin", "mgmt"):
        for key in SERVICE_KEYS:
            got = doc["revenue"][acct][key]["act"]
            want = orig[acct][key]["act"]
            for i in range(min(aui, 12)):
                o = want[i]
                if o == 0:
                    if got[i] != 0:
                        bad.append("%s.%s.act[%d]: got=%d orig=0" % (acct, key, i, got[i]))
                    continue
                diff = abs(got[i] - o) / abs(float(o))
                if diff <= 0.005:
                    continue
                cell = (acct, key, i)
                if cell in KNOWN_ACT_REVISIONS:
                    warned.append("%s.%s.act[%d]: got=%d orig=%d (%.2f%%) — %s"
                                  % (acct, key, i, got[i], o, diff * 100, KNOWN_ACT_REVISIONS[cell]))
                else:
                    bad.append("%s.%s.act[%d]: got=%d orig=%d (%.2f%%)"
                               % (acct, key, i, got[i], o, diff * 100))
    r.check(not bad,
            "(c) 実績確定月（0..%d）の act が原本と ±0.5%% 以内" % (aui - 1),
            "\n        ".join(bad[:10]))
    for w in warned:
        r.warn("(c) 既知のシート改訂により原本と乖離", w)


# ---------------------------------------------------------------------------
# (d) STEP の p
# ---------------------------------------------------------------------------

def test_step_p(r, doc):
    bad, warned = [], []
    for lane in ("online", "kyoten"):
        step = dict((s["n"], s) for s in doc["lks"][lane]["step"])
        for name, want in ORIG_STEP_P[lane].items():
            if name not in step:
                bad.append("lks.%s.step に『%s』行がありません" % (lane, name))
                continue
            got = step[name]["p"]
            same = (round(float(got), 1) == round(float(want), 1))
            if same:
                continue
            if (lane, name) in KNOWN_STEP_P_CHANGES:
                warned.append("lks.%s.step[%s].p: got=%s orig=%s — %s"
                              % (lane, name, got, want, KNOWN_STEP_P_CHANGES[(lane, name)]))
            else:
                bad.append("lks.%s.step[%s].p: got=%s orig=%s" % (lane, name, got, want))
    r.check(not bad, "(d) STEP の p が原本と一致（シートに残っている項目）",
            "\n        ".join(bad[:10]))
    for w in warned:
        r.warn("(d) シート側で目標が引き直されたため原本と不一致", w)
    # 原本注記どおり拠点成約 p=148 を正としているか
    kp = dict((s["n"], s) for s in doc["lks"]["kyoten"]["step"])
    r.check(kp["成約"]["p"] == 148 and doc["lks"]["targets"]["kyoten"] == 148,
            "(d) 拠点の月目標は 単月確認用 148 件（原本注記どおり）",
            "step成約p=%s targets.kyoten=%s" % (kp["成約"]["p"], doc["lks"]["targets"]["kyoten"]))
    on = dict((s["n"], s) for s in doc["lks"]["online"]["step"])
    r.check(on["成約"]["p"] == doc["lks"]["targets"]["online"] == 868,
            "(d) オンラインの月目標は 868 件",
            "step成約p=%s targets.online=%s" % (on["成約"]["p"], doc["lks"]["targets"]["online"]))


# ---------------------------------------------------------------------------
# (e) パーサ単体テスト
# ---------------------------------------------------------------------------

def test_parser(r):
    cases = [
        ("¥382,595,924", 382595924.0),
        ("382,595,924", 382595924.0),
        ("\\-¥122,881", -122881.0),
        ("\\-3,666,436", -3666436.0),
        ("▲1,200", -1200.0),
        ("△0.5", -0.5),
        ("(1,500)", -1500.0),
        ("48.40%", 48.4),
        ("\\-0.40%", -0.4),
        ("99.08%", 99.08),
        ("¥0", 0.0),
        ("0", 0.0),
        ("438.0166667", 438.0166667),
        ("\\#REF\\!", None),
        ("\\#DIV/0\\!", None),
        ("\\#N/A", None),
        ("\\[merged\\]", None),
        ("\\[merged\\] 2", None),
        ("\\[merged\\] 2026-09", None),
        ("", None),
        ("   ", None),
        ("\\-", None),
        ("合計", None),
        ("¥26,500,000", 26500000.0),
    ]
    bad = []
    for src, want in cases:
        got = E.num(src)
        if want is None:
            if got is not None:
                bad.append("num(%r)=%r (期待 None)" % (src, got))
        elif got is None or abs(got - want) > 1e-6:
            bad.append("num(%r)=%r (期待 %r)" % (src, got, want))
    r.check(not bad, "(e) num(): 数値表記の正規化", "\n        ".join(bad))

    bad = []
    for src, want in [("\\_LKS", "_LKS"), ("\\_グロスタ", "_グロスタ"),
                      ("Ａヨミ", "Aヨミ"), ("COST（広告のみ）", "COST(広告のみ)"),
                      ("体験レッスン 参加率", "体験レッスン参加率"),
                      ("\\[merged\\] 2026-09", "[merged]2026-09")]:
        got = E.norm(src)
        if got != want:
            bad.append("norm(%r)=%r (期待 %r)" % (src, got, want))
    r.check(not bad, "(e) norm(): 見出し照合用の正規化", "\n        ".join(bad))

    bad = []
    for src, want in [("2026-09", "2026-09"), ("2026/9", "2026-09"), ("2026/09", "2026-09"),
                      ("\\[merged\\] 2026-09", "2026-09"), ("2026/9/1", "2026-09"),
                      ("FY2026", None), ("通期合計", None)]:
        got = E.parse_ym(src)
        if got != want:
            bad.append("parse_ym(%r)=%r (期待 %r)" % (src, got, want))
    r.check(not bad, "(e) parse_ym(): 年月の解釈", "\n        ".join(bad))

    bad = []
    if E.parse_date("2026/9/7") != _dt.date(2026, 9, 7):
        bad.append("parse_date('2026/9/7')")
    if E.parse_date("2026/9") is not None:
        bad.append("parse_date('2026/9') が None でない")
    if E.parse_date("週番号") is not None:
        bad.append("parse_date('週番号') が None でない")
    r.check(not bad, "(e) parse_date(): 日付の解釈", "; ".join(bad))

    months, label = E.fy_months("2026-09")
    r.check(months[0] == "2026-04" and months[-1] == "2027-03" and label == "FY26",
            "(e) fy_months(): 4月開始年度の 12ヶ月", "%s %s" % (label, months))
    months2, label2 = E.fy_months("2027-02")
    r.check(months2[0] == "2026-04" and label2 == "FY26",
            "(e) fy_months(): 1〜3月は前年度に属する", "%s %s" % (label2, months2))

    # ブロック分割（区切り行を落とし、列位置は保つ）
    md = ("|  | a |  | b |\n| :-: | :-: | :-: | :-: |\n| x | 1 |  | 2 |\n"
          "\n"
          "| c |\n| 3 |\n")
    blocks = []
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(md)
        tmp = fh.name
    try:
        blocks = E.load_blocks(tmp)
    finally:
        os.unlink(tmp)
    ok = (len(blocks) == 2
          and blocks[0].rows == [["", "a", "", "b"], ["x", "1", "", "2"]]
          and blocks[0].cell(1, 3) == "2"
          and blocks[1].rows == [["c"], ["3"]])
    r.check(ok, "(e) load_blocks(): 空行で分割・区切り行除去・列位置保持",
            repr([b.rows for b in blocks]))

    # Ａヨミ表の 1 列ズレ補正
    ctx = E.Ctx()
    months3 = E.fy_months("2026-09")[0]
    raw = [7851000, 12177000, 7453750, 16479766, 12450214, 13965600, 23900300,
           19000000, 19000000, 19000000, 19000000, 19000000]
    actual = [12177000, 7453750, 16479766, 12450214, 13965600, 0, 0, 0, 0, 0, 0, 0]
    shifted, sh = E.align_series(raw, actual, 5, months3, "fin", "grs", ctx)
    r.check(sh == 1 and shifted[5] == 23900300 and shifted[11] is None,
            "(e) align_series(): Ａヨミ表グロスタ行の 1 列ズレを検出して補正",
            "shift=%s shifted=%s" % (sh, shifted))
    ctx2 = E.Ctx()
    same = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    kept, sh2 = E.align_series(same, same, 5, months3, "fin", "lks", ctx2)
    r.check(sh2 == 0 and kept == same,
            "(e) align_series(): ズレていない行は動かさない", "shift=%s" % sh2)


# ---------------------------------------------------------------------------
# 追加: 主要値の見える化
# ---------------------------------------------------------------------------

def dump_summary(doc):
    print("")
    print("---- latest.json の主要値 ----")
    print("basis_date=%s / data_through=%s / target_month=%s / elapsed=%d / remaining=%d / AUI=%d"
          % (doc["basis_date"], doc["data_through"], doc["target_month"],
             doc["elapsed_days"], doc["remaining_days"], doc["actual_until_index"]))
    print("revenue.fin.total.act[5]  = %d" % doc["revenue"]["fin"]["total"]["act"][5])
    print("revenue.fin.grs.act[5]    = %d" % doc["revenue"]["fin"]["grs"]["act"][5])
    print("revenue.mgmt.grs.act[5]   = %d" % doc["revenue"]["mgmt"]["grs"]["act"][5])
    print("lks.online  yomi=%s act=%s  daily長さ=%d  (null=%d)"
          % (doc["lks"]["online"]["yomi"], doc["lks"]["online"]["act"],
             len(doc["lks"]["online"]["daily"]["v"]),
             sum(1 for v in doc["lks"]["online"]["daily"]["v"] if v is None)))
    print("lks.kyoten  yomi=%s act=%s  daily長さ=%d  (null=%d)"
          % (doc["lks"]["kyoten"]["yomi"], doc["lks"]["kyoten"]["act"],
             len(doc["lks"]["kyoten"]["daily"]["v"]),
             sum(1 for v in doc["lks"]["kyoten"]["daily"]["v"] if v is None)))
    print("targets = online:%s / kyoten:%s"
          % (doc["lks"]["targets"]["online"], doc["lks"]["targets"]["kyoten"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="latest.json と extract.py の自己検査")
    ap.add_argument("--json", default=os.path.join(HERE, "data", "latest.json"))
    ap.add_argument("--original", default=os.path.join(HERE, "reference",
                                                       "original-2026-09-08.html"))
    args = ap.parse_args(argv)

    if not os.path.exists(args.json):
        print("エラー: %s がありません。先に python3 zensha/extract.py を実行してください。"
              % args.json, file=sys.stderr)
        return 2
    with open(args.json, encoding="utf-8") as fh:
        doc = json.load(fh)
    orig = load_original_rev(args.original)

    r = Runner()
    print("==== (a) CONTRACT の不変条件 ====")
    test_invariants(r, doc)
    print("")
    print("==== (b) 期初計画 plan の原本一致 ====")
    test_plan_exact(r, doc, orig)
    print("")
    print("==== (c) 実績確定月 act の原本一致（±0.5%） ====")
    test_act_tolerance(r, doc, orig)
    print("")
    print("==== (d) STEP の p ====")
    test_step_p(r, doc)
    print("")
    print("==== (e) パーサ単体テスト ====")
    test_parser(r)

    dump_summary(doc)
    print("")
    print("==== 結果: PASS %d / FAIL %d / WARN %d ====" % (r.passed, r.failed, r.warned))
    if r.failed:
        print("テストに失敗しました。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
