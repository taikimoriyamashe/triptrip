#!/usr/bin/env python3
"""Build dashboard/data/latest.json from raw BigQuery REST outputs (rows[].f[].v + schema.fields).

Typed conversion mirrors the page's live-path parseBqResult(schema, rows):
  INTEGER/INT64 -> int, FLOAT/FLOAT64 -> float, NUMERIC/BIGNUMERIC -> float,
  BOOLEAN/BOOL -> bool, everything else -> str; null -> None.
Also runs the data-contract invariant checks and prints a verification summary.
"""
import calendar
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 生レスポンスの置き場。引数 --raw-dir か環境変数 RAW_DIR で指定(既定: このファイルと同階層の raw/)
import os as _os
_default_raw = Path(__file__).resolve().parent / "raw"
def _arg_path(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 >= len(sys.argv):
            sys.exit(f"ERROR: {flag} には値が必要です")
        return Path(sys.argv[i + 1])
    return default

_default_raw = _arg_path("--raw-dir", Path(_os.environ["RAW_DIR"]) if _os.environ.get("RAW_DIR") else _default_raw)
RAW_DIR = _default_raw
# 出力先。--out で上書き可(既定: このファイルと同階層の data/latest.json)
OUT_PATH = _arg_path("--out", Path(__file__).resolve().parent / "data" / "latest.json")
# targets.json(人が編集するリポジトリ内ファイル)。--targets で上書き可 [v1.2]
TARGETS_PATH = _arg_path("--targets", Path(__file__).resolve().parent / "data" / "targets.json")

QUERIES = [
    "q1_official_monthly",
    "q2_lks_pending",
    "q3_mny_pd_pending",
    "q4_lks_channel_booked",
    "q5_conv_profile",
    "q6_conv_actuals",
    "q7_conv_plan",
    "q8_yomi",
]

# targets.json 不在時の契約既定値 [v1.2]
DEFAULT_TARGETS = {
    "fa_targets": {"lks": None, "mny": None, "pd": None, "total": None},
    "kyoten_conv_target": None,
    "yomi": {"comparable": False,
             "note": "社内売上ヨミ(オンライン)は財務会計と定義が異なる可能性があるため参考値"},
    "note": "fa_targets は月次の目標FA(円)。null=未設定(UIは前月実績を基準線として表示)。",
}

INT_TYPES = {"INTEGER", "INT64"}
FLOAT_TYPES = {"FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
BOOL_TYPES = {"BOOLEAN", "BOOL"}


def convert_value(v, bq_type):
    if v is None:
        return None
    t = bq_type.upper()
    if t in INT_TYPES:
        return int(v)
    if t in FLOAT_TYPES:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            raise ValueError(f"non-finite float not representable in JSON: {v!r}")
        return f
    if t in BOOL_TYPES:
        return v if isinstance(v, bool) else str(v).lower() == "true"
    return str(v)


def parse_result(raw):
    fields = raw["schema"]["fields"]
    rows = []
    for r in raw.get("rows", []):
        cells = r["f"]
        assert len(cells) == len(fields), f"cell/field count mismatch: {len(cells)} vs {len(fields)}"
        rows.append({fld["name"]: convert_value(cell["v"], fld["type"]) for fld, cell in zip(fields, cells)})
    return rows


# q5(前月プロファイル前提)とq6(当月成約)は月初1日に0行になり得る正規の状態 → 警告のみ。
# q7/q8(社内計画・ヨミ)は補助データ: 0行や取得失敗(rawファイル不在)でも既存6本の計算は成立する → 警告のみ [v1.2]
MAY_BE_EMPTY = {"q5_conv_profile", "q6_conv_actuals", "q7_conv_plan", "q8_yomi"}


def main():
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    # 月境界レース回避ガード: JST 00:00〜00:10 は「クエリ実行日」と「スナップショット日付」が
    # 月を跨いで食い違い得るため中断する(契約v1.1 / R1裁定)。
    if now.hour == 0 and now.minute < 10:
        print(f"ABORT: JST {now.isoformat(timespec='seconds')} is within 00:00-00:10 "
              "(month-boundary race guard). Re-run after 00:10 JST.", file=sys.stderr)
        return 2
    # 月境界レースの残穴対応: raw ファイルの取得月(mtime, JST)が basis_date の年月と一致しない場合は中断。
    # (例: raw を前日23時台に取得 → build を翌月00:15 に実行、のすり抜けを防ぐ)
    for name in QUERIES:
        p = RAW_DIR / f"{name}.json"
        if not p.exists() and name in MAY_BE_EMPTY:
            continue  # 補助クエリの取得失敗は読み込み時に警告して空扱い [v1.2]
        mtime_jst = datetime.fromtimestamp(p.stat().st_mtime, jst)
        if (mtime_jst.year, mtime_jst.month) != (now.year, now.month):
            print(f"ABORT: {p.name} was fetched at JST {mtime_jst.isoformat(timespec='seconds')} "
                  f"(month {mtime_jst.strftime('%Y-%m')}), which differs from basis month "
                  f"{now.strftime('%Y-%m')} (month-boundary race guard). Re-fetch the raw results.",
                  file=sys.stderr)
            return 2
    basis_date = now.strftime("%Y-%m-%d")
    target_month = now.strftime("%Y-%m")

    queries = {}
    bytes_processed = {}
    for name in QUERIES:
        p = RAW_DIR / f"{name}.json"
        if not p.exists() and name in MAY_BE_EMPTY:
            print(f"WARNING: {p.name} missing (fetch failed?) — treating {name} as 0 rows")
            queries[name] = {"rows": []}
            bytes_processed[name] = 0
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw.get("jobComplete") is True, f"{name}: job not complete"
        queries[name] = {"rows": parse_result(raw)}
        bytes_processed[name] = int(raw["totalBytesProcessed"])

    # targets.json を注入 [v1.2](不在時は契約既定値で警告)
    if TARGETS_PATH.exists():
        targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    else:
        targets = DEFAULT_TARGETS
        print(f"WARNING: {TARGETS_PATH} not found — injecting contract default targets")

    snapshot = {
        "generated_at": now.isoformat(timespec="seconds"),
        "basis_date": basis_date,
        "target_month": target_month,
        "queries": queries,
        "targets": targets,
        "meta": {"bytes_processed": bytes_processed},
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH}")

    # ---- contract column check ----
    expected_cols = {
        "q1_official_monthly": ["year_month", "lks", "mny", "pd"],
        "q2_lks_pending": ["plan_name", "payment_type", "fa_per_day_cur", "fa_per_day_prev",
                           "n_window", "window_days", "n_lag", "lag_days"],
        "q3_mny_pd_pending": ["service_key", "fa_per_day_cur", "fa_per_day_prev", "n_window",
                              "window_days", "n_lag", "lag_days", "n_active_cur", "n_active_prev"],
        "q4_lks_channel_booked": ["channel", "n_users", "booked_fa"],
        "q5_conv_profile": ["channel", "dom", "n_conv", "fa_per_conv"],
        "q6_conv_actuals": ["channel", "dom", "n_all", "n_valid", "booked_fa_valid"],
        "q7_conv_plan": ["month", "plan_regular", "plan_sutara", "src_regular", "src_sutara", "as_of"],
        "q8_yomi": ["month", "yomi_total", "as_of"],
    }
    for name, cols in expected_cols.items():
        rows = queries[name]["rows"]
        if not rows:
            if name in MAY_BE_EMPTY:
                print(f"WARNING: {name} returned 0 rows — skipping column check")
                continue
            raise AssertionError(f"{name}: no rows")
        for row in rows:
            assert list(row.keys()) == cols, f"{name}: columns {list(row.keys())} != contract {cols}"
    print("column contract: OK (all 8 queries)")

    # ---- invariants ----
    q1 = queries["q1_official_monthly"]["rows"]
    q2 = queries["q2_lks_pending"]["rows"]
    q3 = queries["q3_mny_pd_pending"]["rows"]
    q4 = queries["q4_lks_channel_booked"]["rows"]
    q5 = queries["q5_conv_profile"]["rows"]
    q6 = queries["q6_conv_actuals"]["rows"]
    q7 = queries["q7_conv_plan"]["rows"]
    q8 = queries["q8_yomi"]["rows"]

    cur = next(r for r in q1 if r["year_month"] == target_month)
    # 前月は basis_date から動的算出(ハードコード除去)
    prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    prev = next((r for r in q1 if r["year_month"] == prev_month), None)

    ok = True

    # 1. sum(q4.booked_fa) ~= q1 current lks (within 1%)
    s4 = sum(r["booked_fa"] for r in q4)
    diff_pct = abs(s4 - cur["lks"]) / cur["lks"] * 100
    print(f"[1] sum(q4.booked_fa)={s4:,} vs q1 lks({target_month})={cur['lks']:,} diff={s4-cur['lks']:,} ({diff_pct:.4f}%) -> {'OK' if diff_pct <= 1 else 'FAIL'}")
    ok &= diff_pct <= 1

    # 2. components non-negative
    def rate(row):
        v = row["fa_per_day_cur"]
        if v is None:
            v = row["fa_per_day_prev"]
        return v if v is not None else 0.0
    p1 = sum(r["window_days"] * rate(r) for r in q2)
    p4 = sum(r["lag_days"] * rate(r) for r in q2)
    neg_fields = [(r, k) for r in q2 + q3 for k in ("n_window", "window_days", "n_lag", "lag_days") if r[k] < 0]
    neg_rates = [r for r in q2 + q3 for k in ("fa_per_day_cur", "fa_per_day_prev") if r[k] is not None and r[k] < 0]
    cond2 = p1 >= 0 and p4 >= 0 and not neg_fields and not neg_rates
    print(f"[2] P1={p1:,.0f} P4={p4:,.0f}, negative count/day fields: {len(neg_fields)}, negative rates: {len(neg_rates)} -> {'OK' if cond2 else 'FAIL'}")
    ok &= cond2

    # 3. q1 の固定アンカー月(契約不変条件3: 2026-07 の2026-08-25検証時点実績)との一致。
    #    許容誤差±0.1%: 公式テーブルは日次バッチで過去月も微修正される(契約既知事項、実測例:
    #    2026-08-26 に 2026-07 lks が −69,493 円 = −0.019% 再計上)。差分は必ず表示し、±0.1%超はFAIL。
    #    q1 の窓は前年度4/1起点のローリングなので、アンカーが窓から外れた月は SKIP(FAILにしない)。
    ANCHOR_MONTH = "2026-07"
    ANCHOR_VALUES = {"lks": 356607091, "mny": 4619550, "pd": 9080741}
    ANCHOR_TOL_PCT = 0.1
    anchor = next((r for r in q1 if r["year_month"] == ANCHOR_MONTH), None)
    if anchor is None:
        print(f"[3] q1 anchor {ANCHOR_MONTH} is outside the rolling window (basis {target_month}) -> SKIP")
    else:
        devs = {k: abs(anchor[k] - v) / v * 100 for k, v in ANCHOR_VALUES.items()}
        cond3 = all(d <= ANCHOR_TOL_PCT for d in devs.values())
        detail = " ".join(f"{k}={anchor[k]:,}({anchor[k] - ANCHOR_VALUES[k]:+,}, {devs[k]:.3f}%)" for k in ("lks", "mny", "pd"))
        print(f"[3] q1 {ANCHOR_MONTH} vs 2026-08-25 anchor (tol ±{ANCHOR_TOL_PCT}%): {detail} -> {'OK' if cond3 else 'FAIL'}")
        ok &= cond3

    # 4. MNY rate in 500..900
    def fmt(v, spec=",.1f"):
        return "NULL" if v is None else format(v, spec)
    mny_row = next(r for r in q3 if r["service_key"] == "money")
    mny_rate = rate(mny_row)
    cond4 = 500 <= mny_rate <= 900
    print(f"[4] MNY fa_per_day_cur={fmt(mny_row['fa_per_day_cur'])} (prev={fmt(mny_row['fa_per_day_prev'])}) -> {'OK' if cond4 else 'FAIL'}")
    ok &= cond4

    # 5. sum(q6.booked_fa_valid) <= q1 current lks
    s6 = sum(r["booked_fa_valid"] for r in q6)
    cond5 = s6 <= cur["lks"]
    print(f"[5] sum(q6.booked_fa_valid)={s6:,} <= q1 lks={cur['lks']:,} -> {'OK' if cond5 else 'FAIL'}")
    ok &= cond5

    # 6. multicreator fa_per_day_cur NULL or 0
    mc = next(r for r in q3 if r["service_key"] == "multicreator")
    cond6 = mc["fa_per_day_cur"] in (None, 0, 0.0)
    print(f"[6] multicreator fa_per_day_cur={mc['fa_per_day_cur']!r} -> {'OK' if cond6 else 'FAIL'}")
    ok &= cond6

    # 7. q7 の前月「実績」行 ≒ q5 のオンライン valid 成約実績(±1%) [v1.2]
    #    (前月行が'ヨミ'のまま・q7空・q5空のときは判定不能なので SKIP)
    q5_online_n = sum(r["n_conv"] for r in q5 if r["channel"] == "オンライン")
    prow7 = next((r for r in q7 if r["month"] == prev_month), None)
    if (prow7 and prow7["src_regular"] == "実績" and prow7["src_sutara"] == "実績"
            and prow7["plan_regular"] is not None and prow7["plan_sutara"] is not None
            and q5_online_n > 0):
        plan_n = prow7["plan_regular"] + prow7["plan_sutara"]
        d7 = abs(plan_n - q5_online_n) / q5_online_n * 100
        cond7 = d7 <= 1
        print(f"[7] q7 {prev_month} actuals {plan_n:.0f} vs q5 online n_conv {q5_online_n} diff={d7:.2f}% -> {'OK' if cond7 else 'FAIL'}")
        ok &= cond7
    else:
        print(f"[7] q7 prev-month ({prev_month}) actuals vs q5 online -> SKIP (row missing, src!='実績', or q5 empty)")

    # ---- summary numbers for the report ----
    print("\n-- summary --")
    print(f"q1 {target_month}: lks={cur['lks']:,} mny={cur['mny']:,} pd={cur['pd']:,}")
    if prev is not None:
        print(f"q1 {prev_month} (前月): lks={prev['lks']:,} mny={prev['mny']:,} pd={prev['pd']:,}")
    else:
        print(f"q1 {prev_month} (前月): not in q1 window")
    # 年度(4月〜3月)サマリ [v1.3]: 当年度実績合計(4月〜前月・確定分)と前年度通期実績
    fy_start_year = now.year if now.month >= 4 else now.year - 1
    def fy_months(start_year):
        return [f"{start_year}-{m:02d}" for m in range(4, 13)] + [f"{start_year + 1}-{m:02d}" for m in range(1, 4)]
    q1map = {r["year_month"]: r for r in q1}
    def fy_sum(months):
        rows = [q1map[m] for m in months if m in q1map]
        return {k: sum(r[k] for r in rows) for k in ("lks", "mny", "pd")}, len(rows)
    cur_fy_actual_months = [m for m in fy_months(fy_start_year) if m < target_month]
    s_cur, n_cur = fy_sum(cur_fy_actual_months)
    print(f"FY{fy_start_year} 実績合計(4月〜前月, {n_cur}ヶ月): lks={s_cur['lks']:,} mny={s_cur['mny']:,} pd={s_cur['pd']:,} "
          f"total={s_cur['lks'] + s_cur['mny'] + s_cur['pd']:,}")
    s_prev_fy, n_prev_fy = fy_sum(fy_months(fy_start_year - 1))
    print(f"FY{fy_start_year - 1} 通期実績({n_prev_fy}ヶ月): lks={s_prev_fy['lks']:,} mny={s_prev_fy['mny']:,} pd={s_prev_fy['pd']:,} "
          f"total={s_prev_fy['lks'] + s_prev_fy['mny'] + s_prev_fy['pd']:,}")
    basis_dom = now.day
    last_dom = calendar.monthrange(now.year, now.month)[1]  # 当月末日を動的算出(30日月対応)
    for r in q2:
        print(f"q2 {r['plan_name']}x{r['payment_type']}: rate_cur={fmt(r['fa_per_day_cur'])} rate_prev={fmt(r['fa_per_day_prev'])} "
              f"n_window={r['n_window']} window_days={r['window_days']} n_lag={r['n_lag']} lag_days={r['lag_days']}")
    print(f"LKS P1 (window_days x rate) = {p1:,.0f}")
    print(f"LKS P4 (lag_days x rate)    = {p4:,.0f}")
    for r in q3:
        print(f"q3 {r['service_key']}: rate_cur={r['fa_per_day_cur']} rate_prev={r['fa_per_day_prev']} n_window={r['n_window']} "
              f"window_days={r['window_days']} n_lag={r['n_lag']} lag_days={r['lag_days']} n_active_cur={r['n_active_cur']} n_active_prev={r['n_active_prev']}")
    mny_p1 = mny_row["window_days"] * mny_rate
    mny_p4 = mny_row["lag_days"] * mny_rate
    print(f"MNY P1m={mny_p1:,.0f} P4m={mny_p4:,.0f}")
    total4 = sum(r["booked_fa"] for r in q4)
    denom = sum(r["booked_fa"] for r in q4 if r["channel"] != "成約記録なし")
    for r in q4:
        share = r["booked_fa"] / denom * 100 if r["channel"] != "成約記録なし" else float("nan")
        print(f"q4 {r['channel']}: n_users={r['n_users']:,} booked_fa={r['booked_fa']:,} share={share:.1f}%")
    # conversion counts
    for ch in ("オンライン", "拠点", "分類不能"):
        n_all = sum(r["n_all"] for r in q6 if r["channel"] == ch)
        n_valid = sum(r["n_valid"] for r in q6 if r["channel"] == ch)
        fa_v = sum(r["booked_fa_valid"] for r in q6 if r["channel"] == ch)
        print(f"q6 {ch}: n_all={n_all} n_valid={n_valid} booked_fa_valid={fa_v:,}")
    # q5 aggregate: online monthly weighted fa_per_conv + k factor
    def wavg(ch):
        n = sum(r["n_conv"] for r in q5 if r["channel"] == ch)
        s = sum(r["n_conv"] * r["fa_per_conv"] for r in q5 if r["channel"] == ch)
        return (s / n if n else None), n
    on_avg, on_n = wavg("オンライン")
    kt_avg, kt_n = wavg("拠点")
    k = kt_avg / on_avg if (on_avg and kt_avg is not None) else None
    print(f"q5 online: n={on_n} wavg_fa_per_conv={fmt(on_avg, ',.0f')} / kyoten: n={kt_n} wavg={fmt(kt_avg, ',.0f')} / k={fmt(k, '.3f')}"
          + (" (fallback 0.94 applies)" if k is None else ""))
    # P2/P3 estimate (logic.js preview, online profile based)
    prof_on = {r["dom"]: r["fa_per_conv"] for r in q5 if r["channel"] == "オンライン"}
    def prof(ch, dom):
        base = prof_on.get(dom, 0.0)
        if ch == "拠点":
            return base * (k if k is not None else 0.94)
        return base
    q6map = {}
    for r in q6:
        q6map[(r["channel"], r["dom"])] = r
    p2 = 0.0
    for (ch, dom), r in q6map.items():
        if dom < basis_dom:
            p2 += max(0.0, r["n_valid"] * prof(ch, dom) - r["booked_fa_valid"])
    paces = {}
    for ch in ("オンライン", "拠点"):
        n_valid = sum(r["n_valid"] for r in q6 if r["channel"] == ch)
        paces[ch] = round(n_valid / max(1, basis_dom - 1), 1)
    p3 = sum(paces[ch] * sum(prof(ch, d) for d in range(basis_dom, last_dom + 1)) for ch in ("オンライン", "拠点"))
    print(f"P2(est)={p2:,.0f} P3(est)={p3:,.0f} paces={paces}")
    pending_low = p1 + p2 + p3
    print(f"pending_low={pending_low:,.0f} central={pending_low + 0.5 * p4:,.0f} high={pending_low + p4:,.0f}")
    print(f"landing central(LKS) = {cur['lks'] + pending_low + 0.5 * p4:,.0f}")
    # q7/q8 と目標差分(参考表示) [v1.2]
    for r in q7:
        print(f"q7 {r['month']}: plan_regular={fmt(r['plan_regular'], ',.0f')} plan_sutara={fmt(r['plan_sutara'], ',.0f')} "
              f"src={r['src_regular']}/{r['src_sutara']} as_of={r['as_of']}")
    for r in q8:
        print(f"q8 {r['month']}: yomi_total={fmt(r['yomi_total'], ',.0f')} as_of={r['as_of']} "
              f"(comparable={targets.get('yomi', {}).get('comparable')})")
    crow7 = next((r for r in q7 if r["month"] == target_month), None)
    if crow7 and crow7["plan_regular"] is not None and crow7["plan_sutara"] is not None:
        plan_cur = crow7["plan_regular"] + crow7["plan_sutara"]
        actual_cur = sum(r["n_valid"] for r in q6 if r["channel"] == "オンライン")
        remaining = last_dom - basis_dom + 1
        need_pace = max(0.0, plan_cur - actual_cur) / max(1, remaining)
        pace_landing = actual_cur + paces["オンライン"] * remaining
        print(f"conv target(online): plan={plan_cur:.0f} actual_valid={actual_cur} remaining_days={remaining} "
              f"need_pace={need_pace:.1f}/day pace_landing={pace_landing:.0f}")
    print(f"bytes_processed: {bytes_processed}")

    print("\nALL INVARIANTS:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
