#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zensha/extract.py — 全社着地モニター用 latest.json 生成器

  python3 zensha/extract.py [--raw-dir zensha/raw] [--out zensha/data/latest.json] [--today YYYY-MM-DD]

入力  : zensha/raw/{keiei,marke,kyoten}.md （Google Drive MCP read_file_content の Markdown ダンプ）
出力  : zensha/data/latest.json （zensha/CONTRACT.md 準拠）

設計方針
  - 表の特定は必ず「見出し文字列」で行う。ブロック番号のハードコード禁止。
  - 数値パースは ¥ , % ▲ △ - #REF! #DIV/0! #N/A [merged] 等の表記に耐える。
  - 欠損は黙って 0 で埋めない。Aヨミ→実績→0 のフォールバック順（CONTRACT）を守り、
    適用した補正は revenue_corrections / extract_log に必ず残す。
  - 不変条件 NG のときは stderr に日本語で原因を出して非0終了。

標準ライブラリのみ使用。
"""

from __future__ import annotations

import argparse
import calendar
import datetime as _dt
import json
import os
import re
import sys
import unicodedata

JST = _dt.timezone(_dt.timedelta(hours=9))

CONTRACT_VERSION = "1.5"

SOURCES = [
    {
        "name": "FY26_経営モニタリング",
        "label": "全社（単月確認用・通期着地見通し）",
        "raw": "keiei",
        "id": "1oIz45k-Qkr8AY3v4zBQAiYHDxvg21nvVPSP5xX5q9pE",
    },
    {
        "name": "26年9月_マーケジスイ進捗管理表",
        "raw": "marke",
        "id": "1a6Aud7H-31gs64lDv2Iyww6DnS98wJcuoOssx6nGhJs",
        # 月ごとにファイルが変わるので name は target_month から組み立て直す
        "name_from_month": "%(yy)d年%(m)d月_マーケジスイ進捗管理表",
    },
    {
        "name": "FY26_拠点モニタリング",
        "raw": "kyoten",
        "id": "11pv1YWcxOvJwJjhsNFZa9zGoy0GCn3bauNtSymMppcg",
    },
]

# 原本 zensha/reference/original-2026-09-08.html の REV_ALL[*][*].plan（不変条件3）。
# 年度ラベル（fy.label）でキーする。未登録の年度では不変条件3 をスキップして警告に降格する。
ORIG_PLAN = {
 "FY26": {
    "fin": {
        "total": [382595924, 385250273, 387443907, 393910701, 399368961, 414385176,
                  418067536, 436844273, 455390590, 469095523, 490456164, 500026193],
        "lks": [339439531, 343200086, 345630985, 350133512, 355646518, 360720325,
                368367082, 381267571, 391253553, 401381594, 414518761, 426447820],
        "mny": [4378598, 4506316, 4526850, 4528604, 4609130, 6710971,
                4871534, 4644022, 4870957, 5024529, 4988683, 7017117],
        "pro": [9979988, 9860288, 10394663, 10728113, 10078313, 10703880,
                11878920, 12682680, 13366080, 13739400, 13398720, 12405240],
        "hjn": [7897807, 5683583, 3791409, 5320472, 4735000, 10850000,
                5450000, 8650000, 15200000, 15950000, 22450000, 17956016],
        "grs": [20900000, 22000000, 23100000, 23200000, 24300000, 25400000,
                27500000, 29600000, 30700000, 33000000, 35100000, 36200000],
    },
    "mgmt": {
        "total": [385856895, 399069476, 397102469, 414401208, 436404254, 449387933,
                  460143875, 475222724, 465599199, 490593815, 503603874, 515982313],
        "lks": [342927730, 357339837, 356171150, 370877592, 390936684, 393401522,
                408322001, 418523282, 399892022, 426596166, 432000751, 444786060],
        "mny": [4298858, 4213556, 4207410, 4315644, 4462570, 7496411,
                4591874, 4577442, 4711177, 5663649, 4669123, 7656237],
        "pro": [9832500, 9832500, 9832500, 10687500, 11970000, 12240000,
                14280000, 13872000, 15096000, 9384000, 9384000, 9384000],
        "hjn": [7897807, 5683583, 3791409, 5320472, 4735000, 10850000,
                5450000, 8650000, 15200000, 15950000, 22450000, 17956016],
        "grs": [20900000, 22000000, 23100000, 23200000, 24300000, 25400000,
                27500000, 29600000, 30700000, 33000000, 35100000, 36200000],
    },
 },
}

# 売上マトリクスのサービス行ラベル → 契約キー
SERVICE_LABELS = {
    "合計": "total",
    "_LKS": "lks",
    "_MNY": "mny",
    "_プロデ": "pro",
    "_法人": "hjn",
    "_グロスタ": "grs",
    "_その他": "grs",   # 通期着地ワイド表では _グロスタ が _その他 と表記される
}
SERVICE_KEYS = ["total", "lks", "mny", "pro", "hjn", "grs"]
COMPONENT_KEYS = ["lks", "mny", "pro", "hjn", "grs"]

ACCOUNT_LABELS = {"財務会計売上": "fin", "管理会計売上": "mgmt"}

DOW_JA = ["月", "火", "水", "木", "金", "土", "日"]

ERROR_TOKENS = {
    "#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NULL!", "#NUM!",
    "#ERROR!", "-", "ー", "－", "N/A", "na",
}


# --------------------------------------------------------------------------
# Markdown ダンプの読み込み
# --------------------------------------------------------------------------

_SEP_RE = re.compile(r"^:?-+:?$")


def text(cell: str) -> str:
    """Markdown のバックスラッシュエスケープを外して trim。"""
    if cell is None:
        return ""
    s = re.sub(r"\\(.)", r"\1", cell)
    return s.strip()


def norm(cell: str) -> str:
    """見出し照合用の正規化（NFKC・空白除去）。"""
    s = text(cell)
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s　]+", "", s)
    return s


def strip_merged(cell: str) -> str:
    """'[merged] 2026-09' → '2026-09'。"""
    s = text(cell)
    s = re.sub(r"\[merged\]", "", s).strip()
    return s


def num(cell: str):
    """セル文字列 → float（%は 0-100 の数値のまま）。数値でなければ None。"""
    raw = text(cell)
    if "[merged]" in raw:
        # 結合セルのマーカー（末尾の数字は結合数）。値としては読まない。
        return None
    s = raw
    if not s:
        return None
    if s in ERROR_TOKENS or s.startswith("#"):
        return None
    neg = False
    while s and s[0] in "▲△":
        neg = True
        s = s[1:].strip()
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    is_pct = s.endswith("%")
    if is_pct:
        s = s[:-1]
    s = (s.replace("¥", "").replace("￥", "").replace("$", "")
          .replace(",", "").replace("，", "")
          .replace("円", "").replace("件", "").replace("人", "")
          .replace(" ", "").replace(" ", "").replace("　", ""))
    s = s.replace("−", "-").replace("–", "-").replace("—", "-").replace("ー", "-")
    if s in ("", "-", "+", "."):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if neg:
        v = -v
    return v


class Block(object):
    """空行で区切られた 1 つの Markdown 表ブロック。列位置は落とさずに保持する。"""

    __slots__ = ("index", "rows", "_normset")

    def __init__(self, index, rows):
        self.index = index
        self.rows = rows
        self._normset = None

    def cell(self, r, c):
        if 0 <= r < len(self.rows) and 0 <= c < len(self.rows[r]):
            return self.rows[r][c]
        return ""

    @property
    def normset(self):
        if self._normset is None:
            s = set()
            for row in self.rows:
                for cv in row:
                    n = norm(cv)
                    if n:
                        s.add(n)
            self._normset = s
        return self._normset

    def has(self, *labels):
        return all(norm(l) in self.normset for l in labels)

    def find_cell(self, label):
        """label に完全一致するセルの (row, col) を返す（先頭一致）。"""
        target = norm(label)
        for r, row in enumerate(self.rows):
            for c, cv in enumerate(row):
                if norm(cv) == target:
                    return r, c
        return None

    def find_row_by_labels(self, labels, maxcol=None):
        """指定ラベルを（どこかの列に）全部含む行番号を返す。"""
        targets = [norm(l) for l in labels]
        for r, row in enumerate(self.rows):
            vals = [norm(cv) for cv in (row if maxcol is None else row[:maxcol])]
            if all(t in vals for t in targets):
                return r
        return None


def detect_truncation(content):
    """
    MCP のダンプがサイズ上限等で途中で切れていないかを判定する。
    返り値: None（問題なし）または理由の文字列。
    """
    if not content:
        return "内容が空"
    lines = content.split("\n")
    last = lines[-1]
    if last.lstrip().startswith("|") and not last.rstrip().endswith("|"):
        return "最終行が表の行の途中で終わっている（閉じの '|' が無い）"
    if not content.endswith("\n"):
        return "末尾に改行が無い（ダンプが途中で打ち切られた疑い）"
    return None


def load_blocks(path):
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    blocks = []
    for i, chunk in enumerate(re.split(r"\n\s*\n", content)):
        if not chunk.strip():
            continue
        rows = []
        for line in chunk.split("\n"):
            if not line.lstrip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and all((not c) or _SEP_RE.match(c) for c in cells):
                continue
            rows.append(cells)
        if rows:
            blocks.append(Block(len(blocks), rows))
    return blocks


# --------------------------------------------------------------------------
# 抽出コンテキスト
# --------------------------------------------------------------------------

class Ctx(object):
    def __init__(self):
        self.log = []
        self.corrections = []
        self.errors = []

    def note(self, msg):
        self.log.append(msg)

    def fix(self, msg):
        self.corrections.append(msg)

    def fail(self, msg):
        self.errors.append(msg)


# --------------------------------------------------------------------------
# 期間まわり
# --------------------------------------------------------------------------

_DATE_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_YM_DASH = re.compile(r"^(\d{4})-(\d{2})$")
_YM_SLASH = re.compile(r"^(\d{4})/(\d{1,2})$")


def parse_date(s):
    m = _DATE_SLASH.match(text(s))
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    t = text(s)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_ym(s):
    t = strip_merged(s)
    for rx in (_YM_DASH, _YM_SLASH):
        m = rx.match(t)
        if m:
            return "%04d-%02d" % (int(m.group(1)), int(m.group(2)))
    m = re.match(r"^(\d{4})/(\d{1,2})/1$", t)
    if m:
        return "%04d-%02d" % (int(m.group(1)), int(m.group(2)))
    return None


def fy_months(target_month):
    """target_month を含む 4月開始年度の 12 ヶ月（YYYY-MM）と FY ラベル。"""
    y, m = int(target_month[:4]), int(target_month[5:7])
    fy_start_year = y if m >= 4 else y - 1
    months = []
    for i in range(12):
        mm = 4 + i
        yy = fy_start_year + (0 if mm <= 12 else 1)
        mm = mm if mm <= 12 else mm - 12
        months.append("%04d-%02d" % (yy, mm))
    return months, "FY%02d" % (fy_start_year % 100)


# --------------------------------------------------------------------------
# keiei: 売上マトリクス（期初計画 / 実績 / Ａヨミ）
# --------------------------------------------------------------------------

_KIND_ALIASES = {
    "期初計画": "plan",
    "実績": "act",
    "Aヨミ": "yomiA",
    "Ａヨミ": "yomiA",
}


def _kind_of(cell):
    return _KIND_ALIASES.get(norm(cell))


def find_revenue_block(blocks, ctx):
    """
    「期初計画 / 実績 / Ａヨミ」の 3 表を持つ売上マトリクスのブロックを見出しで特定する。
    条件: 財務会計売上・管理会計売上の行があり、YYYY-MM が 12 個並ぶ月見出し行を持つ。
    """
    best = None
    for b in blocks:
        if not b.has("財務会計売上", "管理会計売上"):
            continue
        if not b.has("_LKS", "_グロスタ", "合計"):
            continue
        tables = parse_revenue_tables(b)
        kinds = set(t["kind"] for t in tables)
        if {"plan", "act", "yomiA"} <= kinds:
            if best is None:
                best = (b, tables)
            else:
                ctx.note("売上マトリクス候補ブロックが複数（block %d も該当）。最初の block %d を使用。"
                         % (b.index, best[0].index))
    return best


def _month_header_row(block, r):
    """行 r が『YYYY-MM が 12 個以上並ぶ月見出し行』なら {col: 'YYYY-MM'} を返す。"""
    row = block.rows[r]
    cols = {}
    for c, cv in enumerate(row):
        ym = parse_ym(cv)
        if ym:
            cols[c] = ym
    if len(cols) < 12:
        return None
    # 連続した 12 列を採用（通期合計などの後続列を排除）
    keys = sorted(cols)
    for i in range(len(keys) - 11):
        window = keys[i:i + 12]
        if window[-1] - window[0] == 11:
            months = [cols[k] for k in window]
            if len(set(months)) == 12:
                return dict(zip(window, months))
    return None


def parse_revenue_tables(block):
    """
    ブロック内の売上サブ表を列挙する。
    返り値: [{'kind':'plan'|'act'|'yomiA', 'months':[...], 'cols':[...], 'data':{acct:{key:[..]}}}]
    kind は月見出し行の直下の行（種別行）が 12 列とも同一種別のときだけ確定させる。
    （2 番目の「実績+修正目標」混在表はここで除外される）
    """
    out = []
    r = 0
    nrows = len(block.rows)
    while r < nrows:
        colmap = _month_header_row(block, r)
        if not colmap:
            r += 1
            continue
        cols = sorted(colmap)
        months = [colmap[c] for c in cols]
        kind = None
        if r + 1 < nrows:
            kinds = [_kind_of(block.cell(r + 1, c)) for c in cols]
            if kinds and all(k is not None and k == kinds[0] for k in kinds):
                kind = kinds[0]
        r_data = r + 2
        data = {}
        acct = None
        while r_data < nrows:
            if _month_header_row(block, r_data):
                break
            row = block.rows[r_data]
            for cv in row[:cols[0]]:
                n = norm(cv)
                if n in ACCOUNT_LABELS:
                    acct = ACCOUNT_LABELS[n]
            key = None
            for cv in row[:cols[0]]:
                n = norm(cv)
                if n in SERVICE_LABELS:
                    key = SERVICE_LABELS[n]
                    break
            if key and acct:
                vals = [num(block.cell(r_data, c)) for c in cols]
                data.setdefault(acct, {}).setdefault(key, vals)
            r_data += 1
        if kind:
            out.append({"kind": kind, "months": months, "cols": cols, "data": data,
                        "row": r, "nseries": sum(len(v) for v in data.values())})
        r = r_data
    return out


def pick_table(tables, kind):
    """同 kind の中で、系列を最も多く含むサブ表を採る。"""
    cands = [t for t in tables if t["kind"] == kind and t["nseries"] >= 6]
    if not cands:
        return None
    cands.sort(key=lambda t: (-t["nseries"], t["row"]))
    return cands[0]


MAX_SHIFT = 2


def align_series(raw_vals, actual_vals, actual_until, months, acct, key, ctx):
    """
    Ａヨミ表の列ズレ（先頭に前年度 3 月の値が入っている等）を検出して補正する。

    実績確定月の実績と突き合わせ、シフト 0..MAX_SHIFT のうち一致数が最大のものを採る（argmax）。
    ただし誤補正を避けるため、シフト 0 に対して十分なマージン
    （max(2, 参照可能月数の半分) 以上の一致数増加）が無ければシフトしない。
    """
    def score(shift):
        hit = 0
        for i in range(min(actual_until, len(actual_vals))):
            a = actual_vals[i]
            b = raw_vals[i + shift] if 0 <= i + shift < len(raw_vals) else None
            if a is None or b is None:
                continue
            if abs(a - b) <= max(1.0, abs(a) * 1e-9):
                hit += 1
        return hit

    n_ref = sum(1 for i in range(min(actual_until, len(actual_vals)))
                if actual_vals[i] is not None)
    if n_ref == 0:
        ctx.note("Ａヨミ表 %s.%s: 実績確定月の実績が全て空のため列ズレ判定ができない（シフトなしで採用）。"
                 % (acct, key))
        return list(raw_vals), 0
    scores = [score(s) for s in range(MAX_SHIFT + 1)]
    best = max(range(len(scores)), key=lambda s: (scores[s], -s))
    margin = max(2, (n_ref + 1) // 2)
    if best > 0 and scores[best] - scores[0] >= margin:
        shifted = list(raw_vals[best:]) + [None] * best
        ctx.fix("Ａヨミ表 %s.%s は %d 列右にズレていたため %d 列左へ戻した"
                "（先頭 %s は %s の値。一致数 shift0=%d → shift%d=%d / 参照%dヶ月）。"
                "末尾 %s は Ａヨミ側に値が無いためフォールバック。"
                % (acct, key, best, best, _fmt_yen(raw_vals[0]), _prev_month(months[0]),
                   scores[0], best, scores[best], n_ref, months[-1]))
        return shifted, best
    if best > 0 and scores[best] > scores[0]:
        ctx.note("Ａヨミ表 %s.%s: シフト%d を検討したが一致 %d/%d（シフト0 は %d/%d）で "
                 "マージン %d 未達のため列ズレ補正を見送った。"
                 % (acct, key, best, scores[best], n_ref, scores[0], n_ref, margin))
    return list(raw_vals), 0


def _prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return "%04d-%02d" % (y, m)


def _fmt_yen(v):
    if v is None:
        return "（空）"
    return "¥{:,.0f}".format(v)


def extract_revenue(keiei_blocks, months, actual_until, ctx):
    found = find_revenue_block(keiei_blocks, ctx)
    if not found:
        ctx.fail("売上マトリクス（財務会計売上/管理会計売上 × 期初計画・実績・Ａヨミ）のブロックが見つかりません。")
        return None, None
    block, tables = found
    ctx.note("売上マトリクス: keiei block %d を『財務会計売上/管理会計売上 + _LKS.._グロスタ + "
             "YYYY-MM 12列』の見出しで特定。" % block.index)

    t_plan = pick_table(tables, "plan")
    t_act = pick_table(tables, "act")
    t_yomi = pick_table(tables, "yomiA")
    for name, t in (("期初計画", t_plan), ("実績", t_act), ("Ａヨミ", t_yomi)):
        if t is None:
            ctx.fail("売上マトリクス内に『%s』表が見つかりません。" % name)
        elif t["months"] != months:
            ctx.fail("『%s』表の月並び %s が年度 %s と一致しません。" % (name, t["months"], months))
    if ctx.errors:
        return None, None
    ctx.note("売上マトリクス: 期初計画=表行%d / 実績=表行%d / Ａヨミ=表行%d（種別見出し行で判定）"
             % (t_plan["row"], t_act["row"], t_yomi["row"]))

    revenue = {}
    for acct in ("fin", "mgmt"):
        plan_d = t_plan["data"].get(acct, {})
        act_d = t_act["data"].get(acct, {})
        yomi_d = t_yomi["data"].get(acct, {})
        series = {}
        for key in SERVICE_KEYS:
            plan = list(plan_d.get(key) or [None] * 12)
            actual = list(act_d.get(key) or [None] * 12)
            yomi_raw = list(yomi_d.get(key) or [None] * 12)
            yomi, _shift = align_series(yomi_raw, actual, actual_until, months, acct, key, ctx)
            series[key] = {"plan": plan, "actual": actual, "yomi": yomi}
        revenue[acct] = series

    out = {}
    for acct in ("fin", "mgmt"):
        out[acct] = {}
        for key in SERVICE_KEYS:
            s = revenue[acct][key]
            plan = []
            for i in range(12):
                v = s["plan"][i]
                if v is None:
                    ctx.fix("%s.%s の期初計画 %s が空のため 0 を採用した。" % (acct, key, months[i]))
                    v = 0.0
                plan.append(int(round(v)))
            # CONTRACT v1.3 のフォールバック順
            #   実績確定月 : 実績 → Ａヨミ → 直前月Ａヨミ → 期初計画 → 0
            #   ヨミ月     : Ａヨミ → 直前月Ａヨミ → 実績 → 期初計画 → 0
            act = []
            for i in range(12):
                if i < actual_until:
                    # 実績確定月では「直前月のヨミ」は使わない（CONTRACT v1.4）
                    chain = [("実績", s["actual"][i]),
                             ("Ａヨミ", s["yomi"][i]),
                             ("期初計画", s["plan"][i])]
                else:
                    chain = [("Ａヨミ", s["yomi"][i]),
                             ("直前月Ａヨミ", s["yomi"][i - 1] if i > 0 else None),
                             ("実績", s["actual"][i]),
                             ("期初計画", s["plan"][i])]
                v, used = None, None
                for nm, cand in chain:
                    if cand is not None:
                        v, used = cand, nm
                        break
                if v is None:
                    v, used = 0.0, "0（全て空）"
                if used != chain[0][0]:
                    ctx.fix("%s.%s %s: %s が空のため『%s』の値 %s を採用"
                            "（CONTRACT v1.3 のフォールバック順 %s）。"
                            % (acct, key, months[i], chain[0][0], used, _fmt_yen(v),
                               " → ".join(nm for nm, _ in chain) + " → 0"))
                act.append(int(round(v)))
            out[acct][key] = {"plan": plan, "act": act}

        # CONTRACT: total.act は 5 サービスの和（シートの合計行はＡヨミ表でグロスタを含まない）
        sheet_total = list(out[acct]["total"]["act"])
        summed = [sum(out[acct][k]["act"][i] for k in COMPONENT_KEYS) for i in range(12)]
        if sheet_total != summed:
            diffs = [i for i in range(12) if sheet_total[i] != summed[i]]
            ctx.fix("%s.total.act はシートの合計行ではなく 5 サービスの和を採用（差異月: %s）。"
                    % (acct, ", ".join(months[i] for i in diffs)))
        out[acct]["total"]["act"] = summed

    detail = {
        "block": block.index,
        "actual_cells": dict(((a, k), list(t_act["data"].get(a, {}).get(k) or [None] * 12))
                             for a in ("fin", "mgmt") for k in SERVICE_KEYS),
        "yomi_aligned": dict(((a, k), list(revenue[a][k]["yomi"]))
                             for a in ("fin", "mgmt") for k in SERVICE_KEYS),
    }
    return out, detail


# --------------------------------------------------------------------------
# keiei: 実績確定月
# --------------------------------------------------------------------------

def parse_wide_tables(keiei_blocks, months, ctx):
    """
    keiei の「実績確定月→」通期着地ワイド表を読む（YYYY/M 見出し・先頭に前年度3月列を持つ）。

    返り値: [{'kinds': {month: '実績'|'Aヨミ'}, 'data': {(acct,key): {month: value}}, 'block': n}]
    サービス行の `_その他` は `grs`（グロースタジオ）に対応させる。
    """
    tables = []
    mset = set(months)
    for b in keiei_blocks:
        # ワイド表の目印: 「実績確定月→」または「2か月前→」（前年度3月列を持つ通期着地表）。
        # 売上マトリクス（YYYY-MM 見出し）はこれらを持たないので混ざらない。
        if not (b.has("実績確定月→") or b.has("2か月前→")):
            continue
        for r, row in enumerate(b.rows):
            colmap = {}
            for c, cv in enumerate(row):
                ym = parse_ym(cv)
                if ym in mset:
                    colmap.setdefault(ym, c)
            if len(colmap) < 12:
                continue
            # 種別行（実績 / Ａヨミ）: 月見出し行の直上 1〜3 行から拾う
            kinds = {}
            for back in range(1, 4):
                rr = r - back
                if rr < 0:
                    break
                got = {}
                for ym, c in colmap.items():
                    k = _kind_of(b.cell(rr, c))
                    if k in ("act", "yomiA"):
                        got[ym] = "実績" if k == "act" else "Aヨミ"
                if len(got) >= 12:
                    kinds = got
                    break
            if not kinds:
                continue
            data = {}
            acct = None
            limit = min(colmap.values())
            for rr in range(r + 1, len(b.rows)):
                vals = [norm(cv) for cv in b.rows[rr][:limit]]
                for n in vals:
                    if n in ACCOUNT_LABELS:
                        acct = ACCOUNT_LABELS[n]
                key = None
                for n in vals:
                    if n in SERVICE_LABELS:
                        key = SERVICE_LABELS[n]
                        break
                if key and acct and (acct, key) not in data:
                    data[(acct, key)] = dict((ym, num(b.cell(rr, c)))
                                             for ym, c in colmap.items())
            if data:
                tables.append({"kinds": kinds, "data": data, "block": b.index})
            break
    return tables


def crosscheck_wide_actual(wide_tables, detail, months, actual_until, ctx):
    """
    不変条件4: 売上マトリクスの実績と「実績確定月→」ワイド表（実績列）の食い違いを extract_log に列挙する。
    """
    # 12ヶ月すべてが「実績」のワイド表を優先（パターン→Ａヨミ版ではなく実績のみの表）
    allact = [t for t in wide_tables if all(t["kinds"].get(m) == "実績" for m in months)]
    partial = [t for t in wide_tables
               if all(t["kinds"].get(m) == "実績" for m in months[:actual_until])]
    picked = allact or partial
    if not picked:
        ctx.note("ワイド表突合: 実績列を持つ「実績確定月→」ワイド表が見つからず、突合をスキップした。")
        return
    # サービス行が多く載っている表を採る（ダンプの途中で切れている表があるため）
    t = max(picked, key=lambda x: len(x["data"]))
    diffs = []
    for acct in ("fin", "mgmt"):
        for key in SERVICE_KEYS:
            w = t["data"].get((acct, key))
            if not w:
                continue
            m_vals = detail["actual_cells"].get((acct, key)) or [None] * 12
            for i in range(actual_until):
                a, bv = m_vals[i], w.get(months[i])
                if a is None or bv is None:
                    continue
                if abs(a - bv) > 1:
                    diffs.append("%s.%s %s: 売上マトリクス=%s / ワイド表=%s"
                                 % (acct, key, months[i], _fmt_yen(a), _fmt_yen(bv)))
    if diffs:
        ctx.note("ワイド表突合（keiei block %d）: 売上マトリクスの実績と食い違う %d 件 → %s"
                 % (t["block"], len(diffs), " ／ ".join(diffs)))
    else:
        ctx.note("ワイド表突合（keiei block %d）: 実績確定月の実績は売上マトリクスと全件一致。" % t["block"])


def crosscheck_wide_yomi_last(wide_tables, revenue, months, ctx):
    """
    「実績確定月→ / パターン→Ａヨミ」ワイド表の年度末（months[-1]）合計から、
    grs の値を逆算して latest.json の grs.act[11] と突合し、extract_log に検算結果を残す。
    """
    last = months[-1]
    picked = [t for t in wide_tables if t["kinds"].get(last) == "Aヨミ"]
    if not picked:
        ctx.note("grs 年度末検算: Ａヨミ列を持つワイド表が見つからず、検算をスキップした。")
        return
    t = picked[0]
    for acct in ("fin", "mgmt"):
        tot = (t["data"].get((acct, "total")) or {}).get(last)
        if tot is None:
            ctx.note("grs 年度末検算 %s: ワイド表（keiei block %d）に %s の合計行が無いためこの表では検算できない"
                     "（ダンプが途中で切れている）。代わりに「通期Σact 突合」で確認する。"
                     % (acct, t["block"], acct))
            continue
        others, missing = 0, []
        for key in ("lks", "mny", "pro", "hjn"):
            v = revenue[acct][key]["act"][11]
            if v is None:
                missing.append(key)
            else:
                others += v
        if missing:
            ctx.note("grs 年度末検算 %s: %s が取れず検算不能。" % (acct, ", ".join(missing)))
            continue
        implied = tot - others
        got = revenue[acct]["grs"]["act"][11]
        mark = "一致" if abs(implied - got) <= 2 else "不一致"
        ctx.note("grs 年度末検算 %s %s（keiei block %d のワイド表 合計 %s − 他4サービス %s "
                 "= %s / latest.json の %s.grs.act[11] = %s → %s）"
                 % (acct, last, t["block"], _fmt_yen(tot), _fmt_yen(others),
                    _fmt_yen(implied), acct, _fmt_yen(got), mark))


def crosscheck_grs_target_month(revenue, month_summary, months, target_month, actual_until, ctx):
    """
    グロスタ列ズレ補正の検算: 補正後の grs.act[当月] が単月確認用の当月 Ａヨミ と一致するか。
    不一致なら致命的エラー（補正が誤っている可能性が高い）。
    """
    if target_month not in months:
        return
    i = months.index(target_month)
    if i < actual_until:
        ctx.note("グロスタ補正の当月検算: %s は実績確定月のためＡヨミ突合をスキップ。" % target_month)
        return
    for acct in ("fin", "mgmt"):
        want = (month_summary.get(acct, {}).get("grs") or {}).get("yomiA")
        got = revenue[acct]["grs"]["act"][i]
        if want is None:
            ctx.note("グロスタ補正の当月検算 %s: 単月確認用に Ａヨミ が無く検算不能。" % acct)
            continue
        if int(want) != int(got):
            ctx.fail("グロスタ列ズレ補正の検算に失敗: %s.grs.act[%s]=%s が単月確認用の当月Ａヨミ %s と一致しません。"
                     % (acct, target_month, _fmt_yen(got), _fmt_yen(want)))
        else:
            ctx.note("グロスタ補正の当月検算 %s: grs.act[%s]=%s は単月確認用の当月Ａヨミと一致。"
                     % (acct, target_month, _fmt_yen(got)))


_FY_LANDING_COLS = {"着地見込み": "landing", "期初計画": "plan"}


def extract_fy_landing(keiei_blocks, target_month, ctx):
    """
    keiei 単月確認用ブロックの「通期＿全社」列群（期初計画 / 着地見込み）を読む。
    返り値: {acct: {key: {"plan": n, "landing": n}}}（見つからなければ None）
    """
    for b in keiei_blocks:
        if not b.find_cell("今月→"):
            continue
        if not b.has("財務会計売上", "管理会計売上", "_LKS", "着地見込み"):
            continue
        hdr = b.find_row_by_labels(["期初計画", "実績", "着地見込み"])
        if hdr is None:
            continue
        # 「[merged] 通期＿全社」の列群を見つける
        group = set()
        for r in range(0, hdr):
            for c, cv in enumerate(b.rows[r]):
                t = text(cv)
                if "merged" in t and "通期" in t:
                    group.add(c)
            if group:
                break
        if not group:
            continue
        cols = {}
        for c in sorted(group):
            lab = _FY_LANDING_COLS.get(norm(b.cell(hdr, c)))
            if lab and lab not in cols:
                cols[lab] = c
        if "landing" not in cols:
            continue
        res, acct = {}, None
        limit = min(cols.values())
        for r in range(hdr + 1, len(b.rows)):
            vals = [norm(cv) for cv in b.rows[r][:limit]]
            for n in vals:
                if n in ACCOUNT_LABELS:
                    acct = ACCOUNT_LABELS[n]
            key = None
            for n in vals:
                if n in SERVICE_LABELS:
                    key = SERVICE_LABELS[n]
                    break
            if not (acct and key) or key in res.get(acct, {}):
                continue
            res.setdefault(acct, {})[key] = dict(
                (lab, num(b.cell(r, c))) for lab, c in cols.items())
        if "fin" in res and "mgmt" in res:
            ctx.note("通期着地見込み: keiei block %d の「[merged] 通期＿全社 → 着地見込み」列から取得"
                     "（Σact の検算用）。" % b.index)
            return res
    ctx.note("通期着地見込み: keiei に「通期＿全社 / 着地見込み」列が見つからず、Σact の検算をスキップ。")
    return None


def crosscheck_fy_landing(revenue, landing, months, ctx):
    """通期着地見込み（シート）と latest.json の Σact を突合して extract_log に残す。"""
    if not landing:
        return
    same, diff = [], []
    for acct in ("fin", "mgmt"):
        for key in SERVICE_KEYS:
            want = (landing.get(acct, {}).get(key) or {}).get("landing")
            if want is None:
                continue
            got = sum(revenue[acct][key]["act"])
            if abs(int(want) - got) <= 1:
                same.append("%s.%s=%s" % (acct, key, _fmt_yen(got)))
            else:
                diff.append("%s.%s: シート着地見込み=%s / Σact=%s（差 %s）"
                            % (acct, key, _fmt_yen(want), _fmt_yen(got), _fmt_yen(got - want)))
    if same:
        ctx.note("通期Σact 突合: シートの「通期＿全社 着地見込み」と一致 %d 系列 → %s"
                 % (len(same), " ／ ".join(same)))
    if diff:
        ctx.note("通期Σact 突合: シートの「通期＿全社 着地見込み」と差がある %d 系列（既知の実績改訂・"
                 "スナップショット差で説明できる範囲。ハード失敗にはしない） → %s"
                 % (len(diff), " ／ ".join(diff)))


def crosscheck_month_summary(revenue, month_summary, months, target_month, ctx):
    """month_summary の yomiA と revenue の当月 act の差を extract_log に残す（検算用）。"""
    if target_month not in months:
        return
    i = months.index(target_month)
    diffs = []
    for acct in ("fin", "mgmt"):
        for key in SERVICE_KEYS:
            want = (month_summary.get(acct, {}).get(key) or {}).get("yomiA")
            got = revenue[acct][key]["act"][i]
            if want is None:
                continue
            if int(want) != int(got):
                diffs.append("%s.%s: 単月確認用yomiA=%s / revenue.act[%s]=%s（差 %s）"
                             % (acct, key, _fmt_yen(want), target_month, _fmt_yen(got),
                                _fmt_yen(got - want)))
    if diffs:
        ctx.note("単月確認用 yomiA と revenue 当月 act の差（%d 件。単月確認用はシートのまま格納、"
                 "表示には revenue を使う）: %s" % (len(diffs), " ／ ".join(diffs)))
    else:
        ctx.note("単月確認用 yomiA と revenue 当月 act は全件一致。")


def extract_actual_until(keiei_blocks, months, ctx):
    for b in keiei_blocks:
        pos = b.find_cell("実績確定月→")
        if not pos:
            continue
        r, c = pos
        for cc in range(c + 1, min(c + 6, len(b.rows[r]))):
            ym = parse_ym(b.cell(r, cc))
            if ym:
                if ym in months:
                    idx = months.index(ym) + 1
                    ctx.note("actual_until_index=%d ← keiei block %d の「実績確定月→ %s」"
                             % (idx, b.index, ym))
                    return idx
                ctx.fail("「実績確定月→ %s」が年度 %s〜%s の範囲外です。" % (ym, months[0], months[-1]))
                return None
    ctx.fail("keiei に「実績確定月→」が見つかりません。actual_until_index を決定できません。")
    return None


# --------------------------------------------------------------------------
# keiei: 単月確認用（month_summary）
# --------------------------------------------------------------------------

_MS_COLS = {"期初計画": "plan", "実績": "act", "Aヨミ": "yomiA", "Ａヨミ": "yomiA", "Bヨミ": "yomiB", "Ｂヨミ": "yomiB"}


def extract_month_summary(keiei_blocks, target_month, ctx):
    for b in keiei_blocks:
        if not b.find_cell("今月→"):
            continue
        if not b.has("財務会計売上", "管理会計売上", "_LKS"):
            continue
        hdr = b.find_row_by_labels(["期初計画", "実績", "Aヨミ", "Bヨミ"])
        if hdr is None:
            hdr = b.find_row_by_labels(["期初計画", "実績", "Ａヨミ", "Ｂヨミ"])
        if hdr is None:
            continue
        # 列 → 月（[merged] YYYY-MM 行）
        group = None
        for r in range(0, hdr):
            m = {}
            for c, cv in enumerate(b.rows[r]):
                if "merged" not in text(cv):
                    continue
                ym = parse_ym(cv)
                if ym:
                    m[c] = ym
            if target_month in m.values():
                group = m
                break
        if group is None:
            continue
        cols = {}
        for c, cv in enumerate(b.rows[hdr]):
            lab = _MS_COLS.get(norm(cv))
            if lab and group.get(c) == target_month and lab not in cols:
                cols[lab] = c
        if not {"plan", "act", "yomiA", "yomiB"} <= set(cols):
            continue
        res = {}
        acct = None
        for r in range(hdr + 1, len(b.rows)):
            row = b.rows[r]
            limit = min(cols.values())
            for cv in row[:limit]:
                n = norm(cv)
                if n in ACCOUNT_LABELS:
                    acct = ACCOUNT_LABELS[n]
            key = None
            for cv in row[:limit]:
                n = norm(cv)
                if n in SERVICE_LABELS:
                    key = SERVICE_LABELS[n]
                    break
            if not (acct and key):
                continue
            entry = res.setdefault(acct, {})
            if key in entry:
                continue
            vals = {}
            for lab, c in cols.items():
                v = num(b.cell(r, c))
                if v is None:
                    ctx.note("month_summary %s.%s の %s が空/エラーのため 0 を採用。" % (acct, key, lab))
                    v = 0.0
                vals[lab] = int(round(v))
            entry[key] = vals
        if "fin" in res and "mgmt" in res:
            ctx.note("month_summary: keiei block %d の『今月→ %s』表（見出し 期初計画/実績/Aヨミ/Bヨミ ×"
                     " [merged] %s 列群）から取得。" % (b.index, target_month, target_month))
            return res
    ctx.fail("keiei の単月確認用（今月→ %s / 期初計画・実績・Aヨミ・Bヨミ）が見つかりません。" % target_month)
    return None


# --------------------------------------------------------------------------
# 日次表（日付行を持つ表の共通ヘルパ）
# --------------------------------------------------------------------------

def date_columns(block, target_month):
    """
    ブロック内の『日付』行を探し、{date: col} を返す。target_month の日付を 5 個以上含む行のみ採用。
    """
    best = None
    for r, row in enumerate(block.rows):
        cols = {}
        for c, cv in enumerate(row):
            d = parse_date(cv)
            if d and "%04d-%02d" % (d.year, d.month) == target_month:
                cols[d] = c
        if len(cols) >= 5 and (best is None or len(cols) > len(best[1])):
            best = (r, cols)
    return best


def row_by_label(block, labels, after=None):
    """指定ラベル列を持つ行番号を返す（ラベルは行内のどこかにあればよい）。"""
    targets = [norm(l) for l in labels]
    for r, row in enumerate(block.rows):
        if after is not None and r <= after:
            continue
        vals = [norm(cv) for cv in row]
        if all(t in vals for t in targets):
            return r
    return None


def read_daily(block, drow_cols, vrow, target_month, elapsed, label, ctx):
    """1〜elapsed 日の値を返す。欠損日は None（0 で埋めない）。"""
    y, m = int(target_month[:4]), int(target_month[5:7])
    vals, missing = [], []
    for day in range(1, elapsed + 1):
        d = _dt.date(y, m, day)
        c = drow_cols.get(d)
        v = num(block.cell(vrow, c)) if c is not None else None
        if v is None:
            missing.append("%d/%d" % (m, day))
            vals.append(None)
        else:
            vals.append(int(round(v)))
    if missing:
        ctx.note("日次 %s: %s の値が空/未入力のため null（0 では埋めない）。" % (label, ", ".join(missing)))
    return vals


# --------------------------------------------------------------------------
# marke: オンライン日次 / KPIサマリ / 期初目標
# --------------------------------------------------------------------------

def extract_online_daily(marke_blocks, target_month, elapsed, ctx):
    cands = []
    for b in marke_blocks:
        found = date_columns(b, target_month)
        if not found:
            continue
        drow, cols = found
        vrow = row_by_label(b, ["成約数", "決済日起点"])
        if vrow is None:
            continue
        cands.append((b, drow, cols, vrow))
    if not cands:
        ctx.fail("marke にオンライン日次表（日付行 + 「成約数 / 決済日起点」行）が見つかりません。")
        return None
    results = []
    for b, drow, cols, vrow in cands:
        results.append((b, read_daily(b, cols, vrow, target_month, elapsed,
                                      "オンライン成約(marke block %d)" % b.index, ctx)))
    base = results[0]
    for b, v in results[1:]:
        if v != base[1]:
            ctx.note("marke のオンライン日次候補が複数あり値が不一致（block %d と block %d）。"
                     "先頭の block %d を採用。" % (base[0].index, b.index, base[0].index))
    ctx.note("lks.online.daily: marke block %d の「成約数 / 決済日起点」行を日付行で列対応させて取得。"
             % base[0].index)
    return base[1]


def extract_kpi_summary(marke_blocks, ctx):
    """
    marke の KPIサマリ（当月目標 / 期中目標 / 昨日までの実績 / 達成率 / 差分 / Aヨミ / …）を読む。
    返り値: dict（下記キー）
    """
    for b in marke_blocks:
        hdr = b.find_row_by_labels(["当月目標", "昨日までの実績", "Aヨミ", "Bヨミ"])
        if hdr is None:
            hdr = b.find_row_by_labels(["当月目標", "昨日までの実績", "Aヨミ"])
        if hdr is None:
            continue
        if not b.has("オンライン成約数", "拠点成約数"):
            continue
        cols = {}
        for c, cv in enumerate(b.rows[hdr]):
            n = norm(cv)
            if n == "当月目標" and "target" not in cols:
                cols["target"] = c
            elif n == "昨日までの実績" and "act" not in cols:
                cols["act"] = c
            elif n in ("Aヨミ", "Ａヨミ") and "yomiA" not in cols:
                cols["yomiA"] = c
            elif n in ("Bヨミ", "Ｂヨミ") and "yomiB" not in cols:
                cols["yomiB"] = c
        if "act" not in cols or "yomiA" not in cols:
            continue

        def read_after(section, label):
            """section 見出し行以降、次のセクション見出しの前にある label 行。"""
            pos = b.find_cell(section)
            if pos is None:
                return None
            sec_row, sec_col = pos
            limit = min(cols.values())
            for r in range(sec_row, len(b.rows)):
                row = b.rows[r]
                if r > sec_row and norm(b.cell(r, sec_col)) and norm(b.cell(r, sec_col)) != norm(section):
                    break
                vals = [norm(cv) for cv in row[:limit]]
                if norm(label) in vals:
                    return dict((k, num(b.cell(r, c))) for k, c in cols.items())
            return None

        out = {}
        # 成約数（全体 / オンライン / 拠点） の「最終成約数」行
        def read_close(section, sub=None):
            pos = b.find_cell(section)
            if pos is None:
                return None
            sec_row, sec_col = pos
            limit = min(cols.values())
            started = sub is None
            for r in range(sec_row, len(b.rows)):
                if r > sec_row and norm(b.cell(r, sec_col)) and norm(b.cell(r, sec_col)) != norm(section):
                    break
                vals = [norm(cv) for cv in b.rows[r][:limit]]
                if sub is not None and norm(sub) in vals:
                    started = True
                if started and "最終成約数" in vals:
                    return dict((k, num(b.cell(r, c))) for k, c in cols.items())
            return None

        out["online_close"] = read_close("オンライン成約数", "全体")
        out["kyoten_close"] = read_close("拠点成約数")
        out["all_close"] = read_close("全体成約数")
        for key, label in (("cost", "cost"), ("apply", "申し込み"), ("cpa", "CPA"),
                           ("book", "予約数"), ("attend_rate", "参加率"),
                           ("attend", "参加数"), ("close_rate", "全体成約率")):
            out["online_" + key] = read_after("オンライン各KPI", label)
        for key, label in (("cost", "cost"), ("apply", "申し込み"), ("cpa", "CPA"),
                           ("book", "予約数"), ("attend_rate", "参加率"),
                           ("attend", "参加数"), ("close_rate", "成約率")):
            out["kyoten_" + key] = read_after("拠点各KPI", label)
        if out["online_close"] and out["kyoten_close"]:
            ctx.note("KPIサマリ: marke block %d（見出し「当月目標/昨日までの実績/Aヨミ/Bヨミ」＋"
                     "「オンライン成約数」「拠点成約数」「オンライン各KPI」「拠点各KPI」）から取得。" % b.index)
            out["_block"] = b.index
            return out
    ctx.fail("marke の KPIサマリ表が見つかりません。")
    return None


_ALLUSER_LABELS = {
    "COST": "cost",
    "体験レッスン申込数(DB)": "apply",
    "申込CPA(DB)": "cpa",
    "体験レッスン予約数(キャンセル含む)": "book",
    "体験レッスン参加率": "attend_rate",
    "体験レッスン参加数": "attend",
    "成約率": "close_rate",
    "最終全体成約数(直CVなど含む)": "close",
}


def extract_online_plan(marke_blocks, ctx):
    """marke の「ALLユーザ」表の『目標』行（期初目標）を読む。"""
    for b in marke_blocks:
        pos = b.find_cell("ALLユーザ")
        if not pos:
            continue
        hr, hc = pos
        cols = {}
        for c, cv in enumerate(b.rows[hr]):
            lab = _ALLUSER_LABELS.get(norm(cv))
            if lab and lab not in cols:
                cols[lab] = c
        if "cost" not in cols or "apply" not in cols:
            continue
        for r in range(hr + 1, min(hr + 6, len(b.rows))):
            vals = [norm(cv) for cv in b.rows[r][:min(cols.values())]]
            if "目標" not in vals:
                continue
            got = dict((k, num(b.cell(r, c))) for k, c in cols.items())
            if got.get("cost") and got.get("apply"):
                ctx.note("オンライン期初目標: marke block %d の「ALLユーザ」見出し行＋『目標』行から取得"
                         "（COST/体験レッスン申込数(DB)/申込CPA(DB)/予約数/参加率/参加数/成約率/成約数）。" % b.index)
                got["_block"] = b.index
                return got
    ctx.fail("marke の「ALLユーザ」期初目標行が見つかりません。")
    return None


# --------------------------------------------------------------------------
# keiei: 拠点ALL 目標（月別）/ オンライン月目標
# --------------------------------------------------------------------------

_KYOTEN_PLAN_LABELS = {
    "申し込み数": "apply",
    "COST(広告のみ)": "cost",
    "申込CPA(広告費のみ)": "cpa",
    "参加率": "attend_rate",
    "参加人数": "attend",
    "成約率": "close_rate",
    "体験レッスン成約数": "close",
}


def extract_kyoten_plan(keiei_blocks, target_month, ctx):
    """keiei の「拠点ALL」月別目標表（受入定員数を含む）から target_month 列を読む。"""
    for b in keiei_blocks:
        if not b.has("拠点ALL", "受入定員数", "体験レッスン成約数"):
            continue
        mrow, mcol = None, None
        for r, row in enumerate(b.rows):
            for c, cv in enumerate(row):
                if parse_ym(cv) == target_month:
                    mrow, mcol = r, c
                    break
            if mrow is not None:
                break
        if mrow is None:
            continue
        out = {}
        for r in range(mrow + 1, len(b.rows)):
            for cv in b.rows[r][:mcol]:
                lab = _KYOTEN_PLAN_LABELS.get(norm(cv))
                if lab and lab not in out:
                    out[lab] = num(b.cell(r, mcol))
        if "close" in out and "apply" in out:
            ctx.note("拠点の月目標: keiei block %d の「拠点ALL」目標表（受入定員数/申し込み数/"
                     "COST（広告のみ）/申込CPA(広告費のみ)/参加率/参加人数/成約率/体験レッスン成約数）"
                     "の %s 列から取得。" % (b.index, target_month))
            out["_block"] = b.index
            return out
    ctx.fail("keiei の「拠点ALL」月別目標表が見つかりません。")
    return None


def extract_online_month_target(keiei_blocks, ctx):
    """keiei の「Month / … / 成約数目標」表の『当月』行から、オンライン月目標（成約数）を読む。"""
    for b in keiei_blocks:
        hdr = b.find_row_by_labels(["Month", "成約数目標", "申込数目標", "売上目標"])
        if hdr is None:
            continue
        cols = {}
        for c, cv in enumerate(b.rows[hdr]):
            n = norm(cv)
            if n in ("成約数目標", "申込数目標", "売上目標") and n not in cols:
                cols[n] = c
        for r in range(hdr + 1, len(b.rows)):
            if norm(b.cell(r, 0)) == "当月":
                v = num(b.cell(r, cols["成約数目標"]))
                if v is not None:
                    ctx.note("オンライン月目標 %d ← keiei block %d の「Month …成約数目標」表『当月』行"
                             "（同行 申込数目標=%s / 売上目標=%s）。"
                             % (int(round(v)), b.index,
                                _fmt_int(num(b.cell(r, cols.get("申込数目標", 0)))),
                                _fmt_yen(num(b.cell(r, cols.get("売上目標", 0))))))
                    return int(round(v)), b.index
    ctx.fail("keiei の「Month / 成約数目標」表『当月』行（オンライン月目標）が見つかりません。")
    return None, None


def _fmt_int(v):
    return "（空）" if v is None else "{:,.0f}".format(v)


# --------------------------------------------------------------------------
# kyoten: 拠点別 CPA（当月目標 / 前月実績）、日次成約
# --------------------------------------------------------------------------

SITES = ["梅田", "福岡", "横浜"]
EXCLUDED_SITES = ["名古屋"]


def extract_sites(kyoten_blocks, target_month, ctx):
    """kyoten の「CPA | N月目標 | N月実績」表から拠点別 CPA を読む。"""
    tm = int(target_month[5:7])
    pm = 12 if tm == 1 else tm - 1
    matched, fallback = None, None
    for b in kyoten_blocks:
        if not b.find_cell("CPA"):
            continue
        for r, row in enumerate(b.rows):
            labs = {}
            for c, cv in enumerate(row):
                n = norm(cv)
                if n == "CPA":
                    labs["cpa"] = c
                m = re.match(r"^(\d{1,2})月目標$", n)
                if m:
                    labs.setdefault("target", (c, int(m.group(1))))
                m = re.match(r"^(\d{1,2})月実績$", n)
                if m:
                    labs.setdefault("prev", (c, int(m.group(1))))
            if "cpa" not in labs or "target" not in labs or "prev" not in labs:
                continue
            sites = []
            for rr in range(r + 1, len(b.rows)):
                head = norm(b.cell(rr, labs["cpa"]))
                if head and head not in SITES and head not in EXCLUDED_SITES:
                    break   # 次の表（COST 表など）に入ったので終了
                name = None
                for cv in b.rows[rr][:labs["cpa"] + 1]:
                    n = norm(cv)
                    if n in SITES or n in EXCLUDED_SITES:
                        name = n
                        break
                if name is None:
                    if sites:
                        break
                    continue
                if name in EXCLUDED_SITES:
                    ctx.note("拠点 %s はクローズのため sites から除外。" % name)
                    continue
                sites.append({
                    "name": name,
                    "cpa_prev": _int_or_none(num(b.cell(rr, labs["prev"][0]))),
                    "cpa_target": _int_or_none(num(b.cell(rr, labs["target"][0]))),
                })
            if len(sites) < 2:
                continue
            hit = (b.index, labs["target"][1], labs["prev"][1], sites)
            if labs["target"][1] == tm:
                matched = matched or hit
            else:
                fallback = fallback or hit
        if matched:
            break
    hit = matched or fallback
    if not hit:
        ctx.fail("kyoten の「CPA / N月目標 / N月実績」拠点別表が見つかりません。")
        return None
    bi, tmon, pmon, sites = hit
    # 目標列の月（tmon）に年を補う。基本は当月と同じ年、年跨ぎだけ前年/翌年に寄せる。
    ty, tm = int(target_month[:4]), int(target_month[5:7])
    ay = ty
    if tmon > tm + 6:
        ay = ty - 1          # 例: 当月 1 月に「12月目標」の表を読んだ
    elif tmon < tm - 6:
        ay = ty + 1          # 例: 当月 12 月に「1月目標」の表を読んだ
    as_of = "%04d-%02d" % (ay, tmon)
    for site in sites:
        site["as_of_month"] = as_of
    if matched:
        ctx.note("拠点別CPA: kyoten block %d の「CPA / %d月目標 / %d月実績」表から取得。" % (bi, tmon, pmon))
        if pmon != pm:
            ctx.note("注意: 拠点別CPA の実績列は『%d月実績』で、前月(%d月)と異なる。" % (pmon, pm))
    else:
        ctx.note("警告: 当月(%d月)の拠点別CPA 表が無いため、keiei block %d の「%d月目標 / %d月実績」表で代用した。"
                 "kyoten シートの月次更新が遅れている可能性がある。" % (tm, bi, tmon, pmon))
    return sites


def _int_or_none(v):
    return None if v is None else int(round(v))


def extract_kyoten_daily(kyoten_blocks, target_month, elapsed, ctx):
    """kyoten の拠点別日次集計（最終_全体成約数）を 3 拠点合算する。"""
    per_site = {}
    for b in kyoten_blocks:
        found = date_columns(b, target_month)
        if not found:
            continue
        drow, cols = found
        vrow = row_by_label(b, ["最終_全体成約数"])
        if vrow is None:
            continue
        site = None
        for nm in SITES + EXCLUDED_SITES:
            if norm(nm) in b.normset:
                site = nm
                break
        if site is None:
            ctx.note("警告: kyoten block %d は日次集計の形（日付行＋「最終_全体成約数」行）だが、"
                     "既知の拠点名（%s / 除外: %s）が見つからない。新拠点が追加された可能性があるため "
                     "extract.py の SITES を確認すること。"
                     % (b.index, "・".join(SITES), "・".join(EXCLUDED_SITES)))
            continue
        if site in EXCLUDED_SITES:
            ctx.note("kyoten block %d は %s のため日次集計から除外（クローズ）。" % (b.index, site))
            continue
        if site in per_site:
            continue
        per_site[site] = read_daily(b, cols, vrow, target_month, elapsed,
                                    "拠点成約 %s (kyoten block %d)" % (site, b.index), ctx)
        ctx.note("拠点日次 %s: kyoten block %d の「最終_全体成約数」行を日付行で列対応させて取得。"
                 % (site, b.index))
    missing = [s for s in SITES if s not in per_site]
    if missing:
        ctx.fail("kyoten の日次集計で拠点 %s の表（日付行 + 「最終_全体成約数」行）が見つかりません。"
                 % "、".join(missing))
        return None
    total = []
    for i in range(elapsed):
        vals = [per_site[s][i] for s in SITES]
        if all(v is None for v in vals):
            total.append(None)
        else:
            total.append(sum(v for v in vals if v is not None))
    return total


# --------------------------------------------------------------------------
# 組み立て
# --------------------------------------------------------------------------

def build_step_online(plan, kpi, target_online, ctx):
    p_attend = plan.get("attend")
    p_book = plan.get("book")
    p_apply = plan.get("apply")
    p_close = float(target_online)
    p_attend_rate = plan.get("attend_rate")
    if p_attend_rate is None and p_attend and p_book:
        p_attend_rate = p_attend / p_book * 100.0
    p_close_rate = (p_close / p_attend * 100.0) if p_attend else None
    kpi_target = (kpi.get("online_close") or {}).get("target")
    note = ("オンラインの月計画(p)は出所が混在: 成約 %d 件は keiei の月目標、"
            "申込・予約・参加・参加率・CPA は marke「ALLユーザ」の目標行（成約 %s 件前提の目標）、"
            "成約率 %.1f%% は 成約p %d ÷ 参加p %s の導出値。"
            % (int(p_close),
               _fmt_int(kpi_target),
               (p_close_rate if p_close_rate is not None else 0.0),
               int(p_close), _fmt_int(p_attend)))
    ctx.note("lks.online.step の p: 参加 p は marke の %s 前提の目標、"
             "成約率 p は %d÷%s で導出（CONTRACT v1.3 の決定どおり）。"
             % (_fmt_int(kpi_target), int(p_close), _fmt_int(p_attend)))
    ctx.note("lks.online.step_note = 「%s」" % note)

    def row(name, p, y, a, **kw):
        d = {"n": name, "p": _num_or_zero(p, name + ".p", ctx),
             "y": _num_or_zero(y, name + ".y", ctx), "a": _num_or_zero(a, name + ".a", ctx)}
        d.update(kw)
        return d

    g = lambda k, f: (kpi.get(k) or {}).get(f)
    return note, [
        row("申込", p_apply, g("online_apply", "yomiA"), g("online_apply", "act")),
        row("予約", p_book, g("online_book", "yomiA"), g("online_book", "act")),
        row("参加", p_attend, g("online_attend", "yomiA"), g("online_attend", "act")),
        row("成約", p_close, g("online_close", "yomiA"), g("online_close", "act"), key=True),
        row("参加率", p_attend_rate, g("online_attend_rate", "yomiA"), g("online_attend_rate", "act"),
            unit="%", sep=True),
        row("成約率", p_close_rate, g("online_close_rate", "yomiA"), g("online_close_rate", "act"), unit="%"),
        row("CPA", plan.get("cpa"), g("online_cpa", "yomiA"), g("online_cpa", "act"),
            unit="¥", lowerBetter=True),
    ]


def build_step_kyoten(plan, kpi, ctx):
    g = lambda k, f: (kpi.get(k) or {}).get(f)

    def row(name, p, y, a, **kw):
        d = {"n": name, "p": _num_or_zero(p, "拠点" + name + ".p", ctx),
             "y": _num_or_zero(y, "拠点" + name + ".y", ctx),
             "a": _num_or_zero(a, "拠点" + name + ".a", ctx)}
        d.update(kw)
        return d

    ctx.note("lks.kyoten.step の p: keiei「拠点ALL」目標表の当月列（申込/参加人数/体験レッスン成約数/"
             "参加率/成約率/申込CPA）。y・a は marke KPIサマリの Aヨミ / 昨日までの実績。")
    return [
        row("申込", plan.get("apply"), g("kyoten_apply", "yomiA"), g("kyoten_apply", "act")),
        row("参加", plan.get("attend"), g("kyoten_attend", "yomiA"), g("kyoten_attend", "act")),
        row("成約", plan.get("close"), g("kyoten_close", "yomiA"), g("kyoten_close", "act"), key=True),
        row("参加率", plan.get("attend_rate"), g("kyoten_attend_rate", "yomiA"),
            g("kyoten_attend_rate", "act"), unit="%", sep=True),
        row("成約率", plan.get("close_rate"), g("kyoten_close_rate", "yomiA"),
            g("kyoten_close_rate", "act"), unit="%"),
        row("CPA", plan.get("cpa"), g("kyoten_cpa", "yomiA"), g("kyoten_cpa", "act"),
            unit="¥", lowerBetter=True),
    ]


def _num_or_zero(v, what, ctx):
    if v is None:
        ctx.note("step %s がシートから取れなかったため 0 を採用。" % what)
        return 0
    v = float(v)
    return int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 2)


# --------------------------------------------------------------------------
# 不変条件
# --------------------------------------------------------------------------

def check_invariants(doc, today, allow_stale, detail, ctx):
    errs = []
    months = doc["fy"]["months"]
    for acct in ("fin", "mgmt"):
        for key in SERVICE_KEYS:
            s = doc["revenue"][acct][key]
            for fld in ("plan", "act"):
                arr = s[fld]
                if len(arr) != 12:
                    errs.append("不変条件1: revenue.%s.%s.%s の長さが %d（12 であるべき）"
                                % (acct, key, fld, len(arr)))
                for i, v in enumerate(arr):
                    if v is None or not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
                        errs.append("不変条件1: revenue.%s.%s.%s[%d] が数値ではありません（%r）"
                                    % (acct, key, fld, i, v))
        for i in range(12):
            tot = doc["revenue"][acct]["total"]["act"][i]
            ssum = sum(doc["revenue"][acct][k]["act"][i] for k in COMPONENT_KEYS)
            if abs(tot - ssum) > 1:
                errs.append("不変条件2: revenue.%s.total.act[%d] (%d) が 5 サービスの和 (%d) と 1 円を超えて乖離"
                            % (acct, i, tot, ssum))

    # 不変条件3: 期初計画は原本 REV_ALL と全12ヶ月厳密一致（基準値は年度ラベルで持つ）
    fy_label = doc["fy"]["label"]
    base = ORIG_PLAN.get(fy_label)
    if base is None:
        msg = ("不変条件3: 年度 %s の期初計画の基準値が extract.py の ORIG_PLAN に未登録のため検査をスキップ"
               "（原本 REV_ALL は %s のもの。年度が変わったら基準値を登録し直すこと）。"
               % (fy_label, "/".join(sorted(ORIG_PLAN)) or "なし"))
        print("警告: " + msg, file=sys.stderr)
        if ctx is not None:
            ctx.note("警告: " + msg)
    else:
        n3 = 0
        for acct in ("fin", "mgmt"):
            for key in SERVICE_KEYS:
                want = base[acct][key]
                got = doc["revenue"][acct][key]["plan"]
                for i in range(12):
                    if got[i] != want[i]:
                        n3 += 1
                        if n3 <= 10:
                            errs.append("不変条件3: revenue.%s.%s.plan[%d]=%d が原本 REV_ALL(%s) の %d と不一致"
                                        % (acct, key, i, got[i], fy_label, want[i]))
        if n3 > 10:
            errs.append("不変条件3: 期初計画の原本不一致が合計 %d 件" % n3)

    aui = doc["actual_until_index"]
    if not isinstance(aui, int) or not (1 <= aui <= 12):
        errs.append("不変条件5: actual_until_index=%r が 1..12 の範囲外" % aui)
    if doc["target_month"] not in months:
        errs.append("不変条件5: target_month=%s が fy.months に含まれない" % doc["target_month"])

    # 不変条件4: 実績確定月の act は正の数値で、売上マトリクス実績表の該当セルと一致
    if detail and isinstance(aui, int) and 1 <= aui <= 12:
        cells = detail.get("actual_cells") or {}
        for acct in ("fin", "mgmt"):
            for key in SERVICE_KEYS:
                got = doc["revenue"][acct][key]["act"]
                raw = cells.get((acct, key)) or [None] * 12
                for i in range(aui):
                    if got[i] < 0:
                        errs.append("不変条件4: revenue.%s.%s.act[%d]=%d が負の数値です（%s）"
                                    % (acct, key, i, got[i], months[i]))
                    elif got[i] == 0 and ctx is not None:
                        ctx.note("不変条件4 参考: %s.%s.act[%s] は ¥0（実績表のセルが 0）。"
                                 "売上が立たなかった月として扱う。" % (acct, key, months[i]))
                    if key == "total":
                        # total は 5 サービスの和（契約）。シートの合計行とは差が出得るのでログのみ。
                        if raw[i] is not None and abs(raw[i] - got[i]) > 1 and ctx is not None:
                            ctx.note("不変条件4 参考: %s.total.act[%s] は 5 サービスの和 %s。"
                                     "シートの合計行は %s（契約どおり和を採用）。"
                                     % (acct, months[i], _fmt_yen(got[i]), _fmt_yen(raw[i])))
                        continue
                    if raw[i] is None:
                        errs.append("不変条件4: revenue.%s.%s.act[%d]（%s）に対応する実績表のセルが空です"
                                    % (acct, key, i, months[i]))
                    elif abs(raw[i] - got[i]) > 1:
                        errs.append("不変条件4: revenue.%s.%s.act[%d]=%d が売上マトリクス実績表のセル %d と不一致"
                                    % (acct, key, i, got[i], int(round(raw[i]))))
        for acct in ("fin", "mgmt"):
            for i in range(aui):
                if all(doc["revenue"][acct][k]["act"][i] == 0 for k in COMPONENT_KEYS):
                    errs.append("不変条件4: revenue.%s の %s は 5 サービスの実績が全て 0 です"
                                "（実績表の読み取りに失敗している可能性）。" % (acct, months[i]))

    for lane in ("online", "kyoten"):
        v = doc["lks"][lane]["daily"]["v"]
        if len(v) != doc["elapsed_days"]:
            errs.append("不変条件6: lks.%s.daily.v の長さ %d が elapsed_days %d と不一致"
                        % (lane, len(v), doc["elapsed_days"]))
        d = doc["lks"][lane]["daily"]
        if not (len(d["dates"]) == len(d["dow"]) == len(v)):
            errs.append("不変条件6: lks.%s.daily の dates/dow/v の長さが揃っていません" % lane)
        for i, x in enumerate(v):
            if x is None:
                continue
            if not isinstance(x, (int, float)) or isinstance(x, bool) or x != x:
                errs.append("不変条件6: lks.%s.daily.v[%d] が数値でも null でもありません（%r）"
                            % (lane, i, x))
        step = doc["lks"][lane]["step"]
        keys = [s for s in step if s.get("key")]
        if len(keys) != 1:
            errs.append("不変条件7: lks.%s.step の key 行が %d 個（1 個であるべき）" % (lane, len(keys)))
        for s in step:
            for f in ("p", "y", "a"):
                if not isinstance(s[f], (int, float)) or isinstance(s[f], bool):
                    errs.append("不変条件7: lks.%s.step[%s].%s が数値ではありません（%r）"
                                % (lane, s["n"], f, s[f]))

    if doc["basis_date"] != today.isoformat():
        msg = ("不変条件8: basis_date=%s が JST の今日 %s と一致しません（古い raw を使っている可能性）"
               % (doc["basis_date"], today.isoformat()))
        if allow_stale:
            print("警告: " + msg + " ※--allow-stale 指定のため不変条件8は警告扱い", file=sys.stderr)
            if ctx is not None:
                ctx.note("警告: " + msg + "（--allow-stale 指定）")
        else:
            errs.append(msg)
    return errs


# --------------------------------------------------------------------------
# sources（raw/<name>.meta.json）
# --------------------------------------------------------------------------

def load_meta(raw_dir, name, ctx):
    """ingest.py が書いた raw/<name>.meta.json を読む（無ければ None）。"""
    path = os.path.join(raw_dir, name + ".meta.json")
    if not os.path.exists(path):
        ctx.note("警告: raw/%s.meta.json がありません。sources は extract.py の既定値で埋めます"
                 "（ingest.py 経由で取り込むと meta が作られます）。" % name)
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
    except ValueError as exc:
        ctx.note("警告: raw/%s.meta.json が読めません（%s）。既定値で埋めます。" % (name, exc))
        return None
    ctx.note("sources: raw/%s.meta.json を使用（title=%s / ingested_at=%s）"
             % (name, m.get("title"), m.get("ingested_at")))
    return m


_MARKE_TITLE_RE = re.compile(r"^(\d{2})年(\d{1,2})月_")


def marke_name_for(target_month, template):
    y, m = int(target_month[:4]), int(target_month[5:7])
    return template % {"yy": y % 100, "m": m}


def build_sources(meta, target_month, ctx):
    out = []
    for src in SOURCES:
        m = meta.get(src["raw"]) or {}
        name = m.get("title") or src["name"]
        sid = m.get("id")
        stale_id = False
        if src.get("name_from_month"):
            built = marke_name_for(target_month, src["name_from_month"])
            if m.get("title"):
                mm = _MARKE_TITLE_RE.match(text(m["title"]))
                if not mm:
                    ctx.note("警告: raw/%s.meta.json の title『%s』から年月を読み取れません。"
                             "sources の name は target_month から組み立てた『%s』を使います。"
                             % (src["raw"], m["title"], built))
                    stale_id = True
                else:
                    ty, tm = 2000 + int(mm.group(1)), int(mm.group(2))
                    if "%04d-%02d" % (ty, tm) != target_month:
                        ctx.note("警告: raw/%s.meta.json の title『%s』の年月が target_month %s と違います。"
                                 "取り込んだファイルが当月分か確認してください（sources の name は『%s』、"
                                 "リンク先 ID も前月分の可能性があります）。"
                                 % (src["raw"], m["title"], target_month, built))
                        stale_id = True
            name = built
            ctx.note("sources: marke の name は target_month(%s) から組み立て →『%s』"
                     % (target_month, name))
            if not sid:
                # 既定値の ID は既定名に焼き込まれた月のもの。その月が当月と同じなら既定値で正しい。
                dm = _MARKE_TITLE_RE.match(text(src["name"]))
                default_month = ("%04d-%02d" % (2000 + int(dm.group(1)), int(dm.group(2)))
                                 if dm else None)
                if default_month == target_month:
                    ctx.note("sources: raw/marke.meta.json が無いため extract.py の既定 ID を使用。"
                             "既定値は %s 分（= target_month と同じ）なのでリンク先は当月分のはず。"
                             % target_month)
                else:
                    stale_id = True
                    ctx.note("警告: marke は月ごとにファイル（＝スプレッドシート ID）が変わりますが、"
                             "raw/marke.meta.json が無く当月の ID を確認できません。sources[marke].url は "
                             "extract.py の既定値（%s 分の ID %s）のままなので、"
                             "『%s』というラベルで前月分のシートを開くリンクになっている可能性があります。"
                             "ingest.py 経由で取り込むと meta から当月の ID が入ります。"
                             % (default_month or "不明", src["id"], name))
        sid = sid or src["id"]
        entry = {"name": name}
        label = m.get("label") or src.get("label")
        if stale_id:
            label = (label or name) + "（リンク先は前月分の可能性）"
        if label:
            entry["label"] = label
        entry["url"] = m.get("url") or ("https://docs.google.com/spreadsheets/d/%s/edit" % sid)
        if stale_id:
            entry["stale_id"] = True
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="全社着地モニターの latest.json を生成する")
    ap.add_argument("--raw-dir", default=os.path.join(here, "raw"))
    ap.add_argument("--out", default=os.path.join(here, "data", "latest.json"))
    ap.add_argument("--today", default=None, help="JST の基準日 (YYYY-MM-DD)。既定は JST の今日。")
    ap.add_argument("--allow-stale", action="store_true",
                    help="raw が古い（basis_date≠JST今日 / marke の対象月が合わない）場合でも警告にとどめる。")
    args = ap.parse_args(argv)

    now = _dt.datetime.now(JST)
    jst_today = now.date()
    if args.today:
        try:
            basis = _dt.date.fromisoformat(args.today)
        except ValueError:
            print("エラー: --today は YYYY-MM-DD 形式で指定してください。", file=sys.stderr)
            return 2
    else:
        basis = jst_today

    ctx = Ctx()
    blocks = {}
    meta = {}
    truncated = []
    for src in SOURCES:
        path = os.path.join(args.raw_dir, src["raw"] + ".md")
        if not os.path.exists(path):
            print("エラー: %s がありません。zensha/ingest.py で raw を用意してください。" % path,
                  file=sys.stderr)
            return 2
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        blocks[src["raw"]] = load_blocks(path)
        ctx.note("読み込み: raw/%s（%d ブロック）"
                 % (os.path.basename(path), len(blocks[src["raw"]])))
        reason = detect_truncation(content)
        if reason:
            truncated.append(src["raw"])
            ctx.note("警告: raw/%s.md はダンプが途中で切れています（%s）。"
                     "表が増えると必要な表が切断点の外に出て、突然取得できなくなる恐れがあります。"
                     % (src["raw"], reason))
        meta[src["raw"]] = load_meta(args.raw_dir, src["raw"], ctx)

    keiei, marke, kyoten = blocks["keiei"], blocks["marke"], blocks["kyoten"]

    data_through = basis - _dt.timedelta(days=1)

    # ---- target_month: marke 日次表の日付行（＝シートが対象としている月）を正とする
    target_month = None
    for b in marke:
        cnt = {}
        for row in b.rows:
            for cv in row:
                d = parse_date(cv)
                if d:
                    ym = "%04d-%02d" % (d.year, d.month)
                    cnt[ym] = cnt.get(ym, 0) + 1
        if cnt:
            top = max(cnt.items(), key=lambda kv: kv[1])
            if top[1] >= 15:
                target_month = top[0]
                ctx.note("target_month=%s ← marke の日次表の日付行（%d 個の日付）で判定。"
                         % (target_month, top[1]))
                break
    if target_month is None:
        target_month = "%04d-%02d" % (data_through.year, data_through.month)
        ctx.note("target_month=%s ← marke から判定できず data_through の月を採用。" % target_month)

    dt_month = "%04d-%02d" % (data_through.year, data_through.month)
    basis_month = "%04d-%02d" % (basis.year, basis.month)
    if target_month not in (dt_month, basis_month):
        msg = ("marke の対象月 %s が data_through(%s) / basis_date(%s) のどちらの月とも一致しません。"
               "古い raw を使っている可能性があります。" % (target_month, dt_month, basis_month))
        if args.allow_stale:
            print("警告: " + msg + " ※--allow-stale 指定のため続行します。", file=sys.stderr)
            ctx.note("警告: " + msg + "（--allow-stale 指定のため続行）")
        else:
            print("エラー: " + msg, file=sys.stderr)
            return 1

    days_in_month = calendar.monthrange(int(target_month[:4]), int(target_month[5:7]))[1]
    first = _dt.date(int(target_month[:4]), int(target_month[5:7]), 1)
    if dt_month == target_month:
        elapsed = data_through.day
    elif data_through < first:
        elapsed = 0
        ctx.note("data_through(%s) が対象月 %s より前のため elapsed_days=0（月替わり直後）。"
                 % (data_through.isoformat(), target_month))
    else:
        elapsed = days_in_month
        ctx.note("data_through(%s) が対象月 %s より後のため elapsed_days=%d（月完了）。"
                 % (data_through.isoformat(), target_month, elapsed))
    remaining = days_in_month - elapsed

    months, fy_label = fy_months(target_month)

    # ---- 鮮度チェック（計算日_as_of）
    for b in keiei:
        pos = b.find_cell("計算日_as_of")
        if pos:
            r, c = pos
            for rr in range(r + 1, min(r + 4, len(b.rows))):
                d = parse_date(b.cell(rr, c))
                if d:
                    if d != basis:
                        ctx.note("注意: keiei の 計算日_as_of=%s が basis_date=%s と異なります（raw の鮮度を確認）。"
                                 % (d.isoformat(), basis.isoformat()))
                    else:
                        ctx.note("鮮度チェック: keiei の 計算日_as_of=%s は basis_date と一致。" % d.isoformat())
                    break
            break

    # ---- 売上
    actual_until = extract_actual_until(keiei, months, ctx)
    revenue, detail = None, None
    if actual_until is not None:
        revenue, detail = extract_revenue(keiei, months, actual_until, ctx)

    # 「実績確定月→」通期着地ワイド表との突合（不変条件4の差異ログ / grs 年度末の検算）
    if revenue is not None:
        wide = parse_wide_tables(keiei, months, ctx)
        crosscheck_wide_actual(wide, detail, months, actual_until, ctx)
        crosscheck_wide_yomi_last(wide, revenue, months, ctx)

    month_summary = extract_month_summary(keiei, target_month, ctx)
    if revenue is not None:
        crosscheck_fy_landing(revenue, extract_fy_landing(keiei, target_month, ctx), months, ctx)
    if revenue is not None and month_summary:
        crosscheck_grs_target_month(revenue, month_summary, months, target_month, actual_until, ctx)
        crosscheck_month_summary(revenue, month_summary, months, target_month, ctx)

    # 単月確認用の期初計画と売上マトリクスの期初計画の突合（ズレていればログに残す）
    if month_summary and revenue and target_month in months:
        ti = months.index(target_month)
        for acct in ("fin", "mgmt"):
            for key in SERVICE_KEYS:
                a = month_summary.get(acct, {}).get(key, {}).get("plan")
                b = revenue.get(acct, {}).get(key, {}).get("plan", [None] * 12)[ti]
                if a is not None and b is not None and a != b:
                    ctx.note("注意: month_summary.%s.%s.plan=%s が売上マトリクスの期初計画 %s と不一致"
                             "（単月確認用シート側の参照ズレ。month_summary はシートのまま格納）。"
                             % (acct, key, _fmt_yen(a), _fmt_yen(b)))

    # ---- 成約KPI
    kpi = extract_kpi_summary(marke, ctx)
    online_plan = extract_online_plan(marke, ctx)
    kyoten_plan = extract_kyoten_plan(keiei, target_month, ctx)
    target_online, tob = extract_online_month_target(keiei, ctx)

    online_daily = extract_online_daily(marke, target_month, elapsed, ctx)
    kyoten_daily = extract_kyoten_daily(kyoten, target_month, elapsed, ctx)
    sites = extract_sites(kyoten, target_month, ctx)

    if ctx.errors:
        print("エラー: シートから必須データを取得できませんでした:", file=sys.stderr)
        for e in ctx.errors:
            print("  - " + e, file=sys.stderr)
        return 1

    dates, dows = [], []
    y, m = int(target_month[:4]), int(target_month[5:7])
    for day in range(1, elapsed + 1):
        d = _dt.date(y, m, day)
        dates.append("%d/%d" % (m, day))
        dows.append(DOW_JA[d.weekday()])

    target_kyoten = int(round(kyoten_plan["close"]))
    if kpi.get("kyoten_close") and kpi["kyoten_close"].get("target") is not None:
        kt = int(round(kpi["kyoten_close"]["target"]))
        if kt != target_kyoten:
            ctx.note("拠点の月目標: 単月確認用（keiei 拠点ALL 目標）%d 件を正とした。"
                     "marke KPIサマリの当月目標は %d 件で不一致（原本注記どおり %d を採用）。"
                     % (target_kyoten, kt, target_kyoten))
    if kpi.get("online_close") and kpi["online_close"].get("target") is not None:
        ot = int(round(kpi["online_close"]["target"]))
        if ot != target_online:
            ctx.note("オンラインの月目標: keiei「成約数目標（当月）」%d 件を正とした。"
                     "marke KPIサマリの当月目標は %d 件で不一致。" % (target_online, ot))

    g = lambda k, f: (kpi.get(k) or {}).get(f)
    online_step_note, online_step = build_step_online(online_plan, kpi, target_online, ctx)

    # 拠点日次の合算と KPIサマリ実績の突合（fail にはしない）
    kp_daily_sum = sum(v for v in (kyoten_daily or []) if v is not None)
    kp_act = _int_or_none(g("kyoten_close", "act"))
    if kp_act is not None:
        ctx.note("拠点実績の突合: 日次集計(梅田・福岡・横浜)の合算 %d 件 と KPIサマリ『昨日までの実績』 %d 件 → %s"
                 % (kp_daily_sum, kp_act,
                    "一致" if kp_daily_sum == kp_act else "不一致（判定=KPIサマリ / グラフ=日次集計）"))
    on_daily_sum = sum(v for v in (online_daily or []) if v is not None)
    on_act = _int_or_none(g("online_close", "act"))
    if on_act is not None:
        ctx.note("オンライン実績の突合: 日次(決済日起点)の合算 %d 件 と KPIサマリ『昨日までの実績』 %d 件 → %s"
                 % (on_daily_sum, on_act,
                    "一致" if on_daily_sum == on_act else "不一致（判定=KPIサマリ / グラフ=日次集計）"))

    doc = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": now.replace(microsecond=0).isoformat(),
        "basis_date": basis.isoformat(),
        "data_through": data_through.isoformat(),
        "target_month": target_month,
        "days_in_month": days_in_month,
        "elapsed_days": elapsed,
        "remaining_days": remaining,
        "fy": {"label": fy_label, "months": months},
        "actual_until_index": actual_until,
        "dump_truncated": bool(truncated),
        "dump_truncated_files": list(truncated),
        "stale_allowed": bool(args.allow_stale),
        "revenue": revenue,
        "revenue_corrections": ctx.corrections,
        "month_summary": month_summary,
        "lks": {
            "targets": {
                "online": target_online,
                "kyoten": target_kyoten,
                "source": ("online=keiei「Month …成約数目標」表の『当月』行（block %s）／"
                           "kyoten=keiei「拠点ALL」目標表の %s 列 体験レッスン成約数（block %s、原本注記どおり"
                           "『単月確認用 %d 件を正』）"
                           % (tob, target_month, kyoten_plan.get("_block"), target_kyoten)),
            },
            "online": {
                "yomi": _int_or_none(g("online_close", "yomiA")),
                "act": _int_or_none(g("online_close", "act")),
                "daily": {"dates": dates, "dow": dows, "v": online_daily},
                "step": online_step,
                "step_note": online_step_note,
                "cost": {
                    "plan": _int_or_none(online_plan.get("cost")),
                    "act": _int_or_none(g("online_cost", "act")),
                    "yomi": _int_or_none(g("online_cost", "yomiA")),
                },
            },
            "kyoten": {
                "yomi": _int_or_none(g("kyoten_close", "yomiA")),
                "act": _int_or_none(g("kyoten_close", "act")),
                "daily": {"dates": dates, "dow": dows, "v": kyoten_daily},
                "step": build_step_kyoten(kyoten_plan, kpi, ctx),
                "cost": {
                    "plan": _int_or_none(kyoten_plan.get("cost")),
                    "act": _int_or_none(g("kyoten_cost", "act")),
                    "yomi": _int_or_none(g("kyoten_cost", "yomiA")),
                },
                "sites": sites,
                "excluded_sites": list(EXCLUDED_SITES),
            },
        },
        "sources": build_sources(meta, target_month, ctx),
        "extract_log": ctx.log,
    }

    errs = check_invariants(doc, jst_today, bool(args.allow_stale), detail, ctx)
    if errs:
        print("エラー: 不変条件を満たしていません:", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        return 1

    outdir = os.path.dirname(os.path.abspath(args.out))
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("生成しました: %s（target_month=%s / data_through=%s / elapsed=%d日 / "
          "actual_until_index=%d）" % (args.out, target_month, data_through.isoformat(),
                                       elapsed, actual_until))
    return 0


if __name__ == "__main__":
    sys.exit(main())
