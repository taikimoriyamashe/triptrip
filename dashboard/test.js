#!/usr/bin/env node
/*
 * test.js — logic.js の契約適合テスト
 *
 *   node dashboard/test.js [fixture.json]
 *
 * フィクスチャの探索順:
 *   1) argv[2]  2) $FA_FIXTURE  3) dashboard/data/latest.json  4) dashboard/fixture.json
 *   5) T2 作成時のフィクスチャ（scratchpad/t2_fixture.json）
 * どれも無ければフィクスチャ依存の不変条件テストは SKIP し、合成データのテストのみ実行する。
 */
"use strict";

var fs = require("fs");
var path = require("path");
var L = require(path.join(__dirname, "logic.js"));

/* ---------------------------------------------------------------- *
 * 最小アサーションハーネス
 * ---------------------------------------------------------------- */
var pass = 0, fail = 0, skip = 0, failures = [];
var group = "";

function describe(name, fn) { group = name; console.log("\n── " + name); fn(); }
function ok(cond, msg, detail) {
  if (cond) { pass++; console.log("  ✓ " + msg); }
  else { fail++; failures.push(group + " / " + msg + (detail ? "  → " + detail : "")); console.log("  ✗ " + msg + (detail ? "  → " + detail : "")); }
}
function skipped(msg) { skip++; console.log("  ~ SKIP " + msg); }
function near(a, b, tol, msg) {
  var t = tol === undefined ? 1e-6 : tol;
  var d = Math.abs(a - b);
  ok(d <= t, msg, "actual=" + a + " expected=" + b + " diff=" + d);
}
function eq(a, b, msg) { ok(a === b, msg, "actual=" + JSON.stringify(a) + " expected=" + JSON.stringify(b)); }

/* ---------------------------------------------------------------- *
 * 1. parseBqResult — 観測済み BigQuery REST 形状
 * ---------------------------------------------------------------- */
describe("parseBqResult — BigQuery REST 形式", function () {
  var raw = {
    jobComplete: true,
    rows: [
      { f: [{ v: "2026-03" }, { v: "356468137" }, { v: "4398540" }, { v: null }] },
      { f: [{ v: "2026-04" }, { v: "351993264" }, { v: "4452110" }, { v: "1234.5" }] }
    ],
    schema: {
      fields: [
        { name: "year_month", type: "STRING" },
        { name: "lks", type: "INTEGER" },
        { name: "mny", type: "INTEGER" },
        { name: "fa_per_day_cur", type: "FLOAT" }
      ]
    }
  };

  var fromObj = L.parseBqResult(raw);
  eq(fromObj.length, 2, "object payload → 2 rows");
  eq(fromObj[0].year_month, "2026-03", "STRING 列は文字列のまま");
  eq(fromObj[0].lks, 356468137, "INTEGER 列は number");
  eq(fromObj[0].fa_per_day_cur, null, "NULL は null");
  eq(fromObj[1].fa_per_day_cur, 1234.5, "FLOAT 列は number");

  var fromStr = L.parseBqResult(JSON.stringify(raw));
  eq(JSON.stringify(fromStr), JSON.stringify(fromObj), "payload が文字列でも同一結果");

  var fromEnvelope = L.parseBqResult({ payload: JSON.stringify(raw), content: [{ type: "text", text: "x" }] });
  eq(JSON.stringify(fromEnvelope), JSON.stringify(fromObj), "CallToolResult ラップ(文字列 payload)でも同一結果");

  var fromEnvelope2 = L.parseBqResult({ payload: raw });
  eq(JSON.stringify(fromEnvelope2), JSON.stringify(fromObj), "CallToolResult ラップ(オブジェクト payload)でも同一結果");

  var split = L.parseBqResult(raw.schema.fields, raw.rows);
  eq(JSON.stringify(split), JSON.stringify(fromObj), "(schema.fields, rows) 2引数形式でも同一結果");

  var flat = L.parseBqResult({ rows: [{ year_month: "2026-05", lks: 1 }] });
  eq(flat[0].year_month, "2026-05", "既に平坦な行はそのまま通す");

  eq(L.parseBqResult(null).length, 0, "null → 空配列");
  eq(L.parseBqResult("not json").length, 0, "壊れた JSON → 空配列（例外を投げない）");
  eq(L.parseBqResult({ jobComplete: true, schema: raw.schema }).length, 0, "rows 欠落 → 空配列");
});

/* ---------------------------------------------------------------- *
 * 2. 手計算ケース（全数値を独立に手計算した合成データ）
 * ---------------------------------------------------------------- */
var MINI = {
  generated_at: "2026-08-10T09:00:00+09:00",
  basis_date: "2026-08-10",
  target_month: "2026-08",
  queries: {
    q1_official_monthly: {
      rows: [
        { year_month: "2026-07", lks: 1000, mny: 200, pd: 300 },
        { year_month: "2026-08", lks: 2000, mny: 400, pd: 600 }
      ]
    },
    q2_lks_pending: {
      rows: [
        { plan_name: "A", payment_type: "月額", fa_per_day_cur: 10, fa_per_day_prev: 5, n_window: 1, window_days: 3, n_lag: 1, lag_days: 2 }
      ]
    },
    q3_mny_pd_pending: {
      rows: [
        { service_key: "money", fa_per_day_cur: null, fa_per_day_prev: 600, n_window: 1, window_days: 2, n_lag: 1, lag_days: 1, n_active_cur: 5, n_active_prev: 4 },
        { service_key: "multicreator", fa_per_day_cur: null, fa_per_day_prev: null, n_window: 2, window_days: 5, n_lag: 0, lag_days: 0, n_active_cur: 4, n_active_prev: 3 }
      ]
    },
    q4_lks_channel_booked: {
      rows: [
        { channel: "オンライン", n_users: 6, booked_fa: 600 },
        { channel: "拠点", n_users: 3, booked_fa: 300 },
        { channel: "分類不能", n_users: 1, booked_fa: 100 },
        { channel: "成約記録なし", n_users: 10, booked_fa: 1000 }
      ]
    },
    q5_conv_profile: {
      rows: [
        { channel: "オンライン", dom: 9, n_conv: 1, fa_per_conv: 200 },
        { channel: "オンライン", dom: 10, n_conv: 2, fa_per_conv: 100 },
        { channel: "オンライン", dom: 11, n_conv: 2, fa_per_conv: 50 },
        { channel: "拠点", dom: 10, n_conv: 1, fa_per_conv: 50 },
        { channel: "拠点", dom: 11, n_conv: 1, fa_per_conv: 25 }
      ]
    },
    q6_conv_actuals: {
      rows: [
        { channel: "オンライン", dom: 9, n_all: 4, n_valid: 3, booked_fa_valid: 100 },
        { channel: "拠点", dom: 9, n_all: 2, n_valid: 2, booked_fa_valid: 50 },
        { channel: "オンライン", dom: 10, n_all: 2, n_valid: 2, booked_fa_valid: 0 }
      ]
    }
  },
  meta: { bytes_processed: {} }
};

describe("手計算ケース — buildModel(paceOn=2, paceKyoten=4, lagWeight=0.5)", function () {
  var m = L.buildModel(MINI, { paceOn: 2, paceKyoten: 4, lagWeight: 0.5 });

  // --- 基準日・月 ---
  eq(m.meta.basisDom, 10, "basis_dom = 10");
  eq(m.meta.lastDom, 31, "last_dom = 31（2026-08）");
  eq(m.meta.prevMonth, "2026-07", "prev_month = 2026-07");
  eq(m.meta.remainingDays, 22, "残日数 = 22（10..31）");

  // --- k = 拠点加重平均 37.5 ÷ オンライン加重平均 100 = 0.375 ---
  near(m.inputs.k, 0.375, 1e-12, "k = 0.375（q5 から算出）");
  eq(m.inputs.kSource, "q5", "k のソースは q5");

  // --- ① P1 = window_days 3 × rate 10 = 30 ---
  near(m.lks.components.p1, 30, 1e-9, "① 更新残 P1 = 30");
  // --- ④ P4 = lag_days 2 × rate 10 = 20 ---
  near(m.lks.components.p4, 20, 1e-9, "④ 滞納/ラグ P4 = 20");
  // --- ② dom9: online 3×200−100=500, 拠点 2×(200×0.375)−50=100 → 600 ---
  near(m.lks.components.p2, 600, 1e-9, "② 既成約未計上 P2 = 600（dom10 は基準日以降なので除外）");
  // --- ③ 残日 prof: online 100+50=150 / 拠点 150×0.375=56.25 → 2×150 + 4×56.25 = 525 ---
  near(m.lks.components.p3, 525, 1e-9, "③ 今後の成約 P3 = 525");

  near(m.lks.pending.low, 1155, 1e-9, "LKS pending low = 30+600+525 = 1155");
  near(m.lks.pending.central, 1165, 1e-9, "LKS pending central = 1155 + 0.5×20 = 1165");
  near(m.lks.pending.high, 1175, 1e-9, "LKS pending high = 1155 + 20 = 1175");
  near(m.lks.landing.low, 3155, 1e-9, "LKS 着地 low = 2000 + 1155");
  near(m.lks.landing.central, 3165, 1e-9, "LKS 着地 central = 2000 + 1165");
  near(m.lks.landing.high, 3175, 1e-9, "LKS 着地 high = 2000 + 1175");

  // --- MNY: rate は cur NULL → prev 600。P1m=2×600=1200, P4m=1×600=600 ---
  eq(m.mny.rateSource, "prev", "MNY の単価は前月フォールバック");
  near(m.mny.components.p1, 1200, 1e-9, "MNY ① = 1200");
  near(m.mny.components.p4, 600, 1e-9, "MNY ④ = 600");
  near(m.mny.landing.low, 1600, 1e-9, "MNY 着地 low = 400 + 1200");
  near(m.mny.landing.central, 1900, 1e-9, "MNY 着地 central = 400 + 1200 + 300");
  near(m.mny.landing.high, 2200, 1e-9, "MNY 着地 high = 400 + 1200 + 600");

  // --- プロデ: perUnit = 前月 pd 300 ÷ n_active_prev 3 = 100 ; upside = 2×100 = 200 ---
  near(m.pd.perUnit, 100, 1e-9, "プロデ 1件単価 = 前月公式 pd ÷ n_active_prev = 100");
  near(m.pd.upside, 200, 1e-9, "プロデ upside = n_window 2 × 100 = 200");
  near(m.pd.landing.low, 600, 1e-9, "プロデ 着地 low = central = 計上済み 600");
  near(m.pd.landing.central, 600, 1e-9, "プロデ 着地 central = 600");
  near(m.pd.landing.high, 800, 1e-9, "プロデ 着地 high = 800");

  // --- 合計 ---
  near(m.total.landing.low, 5355, 1e-9, "合計 着地 low = 3155+1600+600");
  near(m.total.landing.central, 5665, 1e-9, "合計 着地 central = 3165+1900+600");
  near(m.total.landing.high, 6175, 1e-9, "合計 着地 high = 3175+2200+800");

  // --- 前月比較 ---
  near(m.total.prev, 1500, 1e-9, "前月合計 = 1000+200+300");
  near(m.lks.deltaPct, 3165 / 1000 - 1, 1e-12, "LKS 前月比 = central/前月 − 1");

  // --- チャネル按分 ---
  var by = {}; m.lks.channels.forEach(function (c) { by[c.channel] = c; });
  near(by["オンライン"].share, 0.6, 1e-12, "オンライン share = 600/1000 = 0.6");
  near(by["拠点"].share, 0.3, 1e-12, "拠点 share = 0.3");
  near(by["分類不能"].share, 0.1, 1e-12, "分類不能 share = 0.1");
  eq(by["成約記録なし"].share, 0, "成約記録なし は share 0（按分対象外）");
  near(by["オンライン"].inc.p1, 18, 1e-9, "オンライン ① 按分 = 0.6×30 = 18");
  near(by["オンライン"].inc.p2, 360, 1e-9, "オンライン ② 按分 = 0.6×600 = 360");
  near(by["オンライン"].inc.p3, 300, 1e-9, "オンライン ③ は直接 = 2×150 = 300");
  near(by["オンライン"].inc.p4, 12, 1e-9, "オンライン ④ 按分 = 0.6×20 = 12");
  near(by["オンライン"].landing.low, 1278, 1e-9, "オンライン 着地 low = 600+678");
  near(by["拠点"].landing.low, 714, 1e-9, "拠点 着地 low = 300+414");
  near(by["分類不能"].landing.low, 163, 1e-9, "分類不能 着地 low = 100+63");
  near(by["成約記録なし"].landing.central, 1000, 1e-9, "成約記録なし 着地 = 計上済みのまま");

  var sumP1 = m.lks.channels.reduce(function (s, c) { return s + c.inc.p1; }, 0);
  var sumP2 = m.lks.channels.reduce(function (s, c) { return s + c.inc.p2; }, 0);
  var sumP3 = m.lks.channels.reduce(function (s, c) { return s + c.inc.p3; }, 0);
  var sumP4 = m.lks.channels.reduce(function (s, c) { return s + c.inc.p4; }, 0);
  near(sumP1, m.lks.components.p1, 1e-6, "チャネル按分 Σ① = 全体①");
  near(sumP2, m.lks.components.p2, 1e-6, "チャネル按分 Σ② = 全体②");
  near(sumP3, m.lks.components.p3, 1e-6, "チャネル別 Σ③ = 全体③");
  near(sumP4, m.lks.components.p4, 1e-6, "チャネル按分 Σ④ = 全体④");
  var sumLandingLow = m.lks.channels.reduce(function (s, c) { return s + c.landing.low; }, 0);
  near(sumLandingLow, m.lks.landing.low, 1e-6, "Σ チャネル着地(low) = LKS 着地(low)（Σq4 = q1 のとき）");

  // --- pace デフォルト ---
  var d = L.defaultPaces(MINI.queries.q6_conv_actuals.rows, 10);
  near(d.paceOn, 0.6, 1e-12, "pace デフォルト(オンライン) = 5 ÷ 9 = 0.6（小数1桁）");
  near(d.paceKyoten, 0.2, 1e-12, "pace デフォルト(拠点) = 2 ÷ 9 = 0.2");
  eq(d.paceUnknown, 0, "pace デフォルト(分類不能) = 0");
});

describe("lagWeight を振ると central だけが動く", function () {
  var a = L.buildModel(MINI, { paceOn: 2, paceKyoten: 4, lagWeight: 0 });
  var b = L.buildModel(MINI, { paceOn: 2, paceKyoten: 4, lagWeight: 1 });
  near(a.lks.landing.central, a.lks.landing.low, 1e-9, "lagWeight=0 → central = low");
  near(b.lks.landing.central, b.lks.landing.high, 1e-9, "lagWeight=1 → central = high");
  near(a.lks.landing.low, b.lks.landing.low, 1e-9, "low は lagWeight に依存しない");
  near(a.lks.landing.high, b.lks.landing.high, 1e-9, "high は lagWeight に依存しない");
});

describe("pace を振ると ③ と着地だけが動く", function () {
  var a = L.buildModel(MINI, { paceOn: 2, paceKyoten: 4, lagWeight: 0.5 });
  var b = L.buildModel(MINI, { paceOn: 4, paceKyoten: 4, lagWeight: 0.5 });
  near(b.lks.components.p3 - a.lks.components.p3, 300, 1e-9, "paceOn +2 → ③ が +2×150 = +300");
  near(b.lks.components.p1, a.lks.components.p1, 1e-9, "① は不変");
  near(b.lks.components.p2, a.lks.components.p2, 1e-9, "② は不変");
  near(b.lks.components.p4, a.lks.components.p4, 1e-9, "④ は不変");
  near(b.lks.landing.central - a.lks.landing.central, 300, 1e-9, "着地 central も +300");
});

/* ---------------------------------------------------------------- *
 * 3. エッジケース
 * ---------------------------------------------------------------- */
function clone(o) { return JSON.parse(JSON.stringify(o)); }

describe("エッジケース", function () {
  // q5 が空
  var noQ5 = clone(MINI); noQ5.queries.q5_conv_profile.rows = [];
  var m5 = L.buildModel(noQ5, { paceOn: 2, paceKyoten: 4 });
  eq(m5.inputs.kSource, "fallback", "q5 が空 → k は実務値フォールバック");
  near(m5.inputs.k, 0.94, 1e-12, "q5 が空 → k = 0.94");
  near(m5.lks.components.p2, 0, 1e-9, "q5 が空 → ② = 0（prof 欠損は 0 扱い・負にならない）");
  near(m5.lks.components.p3, 0, 1e-9, "q5 が空 → ③ = 0");
  near(m5.lks.landing.central, 2000 + 30 + 10, 1e-9, "q5 が空でも ①④ から着地は算出できる");

  // q5 に該当 dom が無い（プロファイル欠損 dom = 0 扱い）
  var gap = clone(MINI);
  gap.queries.q5_conv_profile.rows = [{ channel: "オンライン", dom: 11, n_conv: 1, fa_per_conv: 50 }];
  var mg = L.buildModel(gap, { paceOn: 1, paceKyoten: 0 });
  near(mg.lks.components.p3, 50, 1e-9, "欠損 dom は 0、dom11 のみ 50 → ③ = 1×50");
  near(mg.lks.components.p2, 0, 1e-9, "dom9 のプロファイルが無い → ② = 0");

  // 月初 edge: fa_per_day_cur が全 NULL でも prev で計算
  var monthStart = clone(MINI);
  monthStart.basis_date = "2026-08-01";
  monthStart.queries.q2_lks_pending.rows = [
    { plan_name: "A", payment_type: "月額", fa_per_day_cur: null, fa_per_day_prev: 5, n_window: 1, window_days: 3, n_lag: 0, lag_days: 0 },
    { plan_name: "B", payment_type: "一括", fa_per_day_cur: null, fa_per_day_prev: 7, n_window: 1, window_days: 10, n_lag: 1, lag_days: 4 }
  ];
  var ms = L.buildModel(monthStart, { paceOn: 0, paceKyoten: 0 });
  eq(ms.meta.basisDom, 1, "月初 basis_dom = 1");
  eq(ms.lks.anyCurRate, false, "当月単価が一つも無い");
  near(ms.lks.components.p1, 3 * 5 + 10 * 7, 1e-9, "① は前月単価で算出 = 85");
  near(ms.lks.components.p4, 4 * 7, 1e-9, "④ も前月単価で算出 = 28");
  near(ms.lks.components.p2, 0, 1e-9, "月初は dom<1 が無いので ② = 0");
  ok(isFinite(ms.total.landing.central), "月初でも合計着地が有限値");

  // cur も prev も NULL → rate 0
  var noRate = clone(MINI);
  noRate.queries.q2_lks_pending.rows = [{ plan_name: "A", payment_type: "月額", fa_per_day_cur: null, fa_per_day_prev: null, n_window: 1, window_days: 3, n_lag: 1, lag_days: 2 }];
  var mnr = L.buildModel(noRate, {});
  near(mnr.lks.components.p1, 0, 1e-9, "cur/prev とも NULL → rate 0 → ① = 0");

  // q6 が空
  var noQ6 = clone(MINI); noQ6.queries.q6_conv_actuals.rows = [];
  var m6 = L.buildModel(noQ6, {});
  near(m6.lks.components.p2, 0, 1e-9, "q6 が空 → ② = 0");
  eq(m6.inputs.paceOn, 0, "q6 が空 → pace デフォルト 0");
  ok(isFinite(m6.total.landing.high), "q6 が空でもクラッシュしない");

  // 全クエリ欠落
  var empty = L.buildModel({}, {});
  ok(isFinite(empty.total.landing.central), "queries 丸ごと欠落でもクラッシュしない");
  eq(empty.lks.channels.length, 4, "チャネルは 0 行でも 4 種そろう");
  var empty2 = L.buildModel(undefined, undefined);
  ok(isFinite(empty2.total.landing.central), "data / opts が undefined でもクラッシュしない");

  // multicreator 行が無い
  var noPd = clone(MINI);
  noPd.queries.q3_mny_pd_pending.rows = noPd.queries.q3_mny_pd_pending.rows.slice(0, 1);
  var mp = L.buildModel(noPd, {});
  near(mp.pd.upside, 0, 1e-9, "multicreator 行が無い → upside 0");
  near(mp.pd.landing.high, 600, 1e-9, "multicreator 行が無い → high = 計上済み");

  // n_active_prev が 0（0除算）
  var zeroActive = clone(MINI);
  zeroActive.queries.q3_mny_pd_pending.rows[1].n_active_prev = 0;
  var mz = L.buildModel(zeroActive, {});
  near(mz.pd.perUnit, 0, 1e-9, "n_active_prev=0 → 単価 0（0除算しない）");
  ok(isFinite(mz.pd.landing.high), "n_active_prev=0 でも有限値");

  // 負の pace は 0 にクランプ
  var mc = L.buildModel(MINI, { paceOn: -5, paceKyoten: 4 });
  eq(mc.inputs.paceOn, 0, "負の pace は 0 にクランプ");
  ok(mc.lks.components.p3 >= 0, "③ は非負");
});

describe("実データ特性 — 未来月の負値 / 0（R1 レビュー由来）", function () {
  var neg = clone(MINI);
  neg.queries.q1_official_monthly.rows.push(
    { year_month: "2026-09", lks: 500, mny: 10, pd: 0 },
    { year_month: "2026-10", lks: 120, mny: -350, pd: 0 },
    { year_month: "2026-11", lks: 30, mny: -420, pd: 0 }
  );
  var m = L.buildModel(neg, { paceOn: 2, paceKyoten: 4 });
  var fut = m.history.filter(function (h) { return h.isFuture; });
  eq(fut.length, 3, "未来月が 3 行");
  eq(fut[1].mny, -350, "未来月の負の mny がそのまま保持される");
  eq(fut[1].pd, 0, "未来月の pd = 0 がそのまま保持される");
  near(fut[1].total, 120 - 350 + 0, 1e-9, "負値を含む合計が正しい");
  ok(m.history.every(function (h) { return isFinite(h.lks) && isFinite(h.mny) && isFinite(h.pd); }),
    "履歴の全値が有限（負値・0 を含む）");
  // 当月の着地は未来月に影響されない
  var base = L.buildModel(MINI, { paceOn: 2, paceKyoten: 4 });
  near(m.total.landing.central, base.total.landing.central, 1e-9, "未来月の行は当月の着地に影響しない");
  var nn = m.notices.filter(function (n) { return n.id === "negative_future"; });
  eq(nn.length, 1, "未来月マイナスの注意が 1 件出る");
  eq(nn[0].level, "info", "未来月マイナスは info レベル");
  // すべての未来月が 0 のケース
  var zero = clone(MINI);
  zero.queries.q1_official_monthly.rows.push({ year_month: "2026-09", lks: 0, mny: 0, pd: 0 });
  var mz = L.buildModel(zero, {});
  eq(mz.history[mz.history.length - 1].total, 0, "全部 0 の未来月でも壊れない");
  eq(mz.notices.filter(function (n) { return n.id === "negative_future"; }).length, 0, "0 は負値扱いしない");
});

describe("月初（basis_dom ≤ 5）の注意喚起", function () {
  eq(L.MONTH_START_DOM, 5, "月初のしきい値は 5 日");

  var d1 = clone(MINI);
  d1.basis_date = "2026-08-01";
  d1.queries.q5_conv_profile.rows = [];   // 月初は前月プロファイルが未生成のことがある
  d1.queries.q6_conv_actuals.rows = [];   // 当月の成約もまだ 0 件
  var m1 = L.buildModel(d1, {});
  eq(m1.meta.basisDom, 1, "1日 → basis_dom = 1");
  eq(m1.meta.isMonthStart, true, "1日は月初判定");
  near(m1.lks.components.p2, 0, 1e-9, "月初は ② が 0");
  near(m1.lks.components.p3, 0, 1e-9, "q5/q6 空なので ③ も 0");
  ok(isFinite(m1.total.landing.central), "q5/q6 が両方空でもクラッシュしない");
  ok(isFinite(m1.total.landing.low) && isFinite(m1.total.landing.high), "low / high も有限");
  eq(m1.lks.channels.length, 4, "チャネルは 4 種そろう");
  eq(m1.lks.conv.byDom.length, 0, "日別成約は 0 行");
  eq(m1.inputs.kSource, "fallback", "q5 空 → k はフォールバック");

  var ids = m1.notices.map(function (n) { return n.id; });
  ok(ids.indexOf("month_start") >= 0, "月初の注意が出る", JSON.stringify(ids));
  ok(ids.indexOf("no_profile") >= 0, "q5 空の注意が出る", JSON.stringify(ids));
  ok(ids.indexOf("no_conv") >= 0, "q6 空の注意が出る", JSON.stringify(ids));
  var ms = m1.notices.filter(function (n) { return n.id === "month_start"; })[0];
  ok(/前月実績/.test(ms.body), "月初の注意文が前月実績への参照を含む");
  ok(ms.body.indexOf("2026-07") >= 0, "月初の注意文に前月が入る");

  [1, 2, 5].forEach(function (dom) {
    var dd = clone(MINI);
    dd.basis_date = "2026-08-0" + dom;
    eq(L.buildModel(dd, {}).meta.isMonthStart, true, dom + "日は月初判定");
  });
  [6, 10].forEach(function (dom) {
    var dd = clone(MINI);
    dd.basis_date = "2026-08-" + (dom < 10 ? "0" + dom : dom);
    eq(L.buildModel(dd, {}).meta.isMonthStart, false, dom + "日は月初ではない");
  });
  eq(L.buildModel(MINI, {}).meta.isMonthStart, false, "10日基準は月初ではない");

  // q5 だけ空 / q6 だけ空 でも落ちない（月初1日に実際に起きる組み合わせ）
  var only5 = clone(MINI); only5.basis_date = "2026-08-01"; only5.queries.q5_conv_profile.rows = [];
  ok(isFinite(L.buildModel(only5, {}).total.landing.high), "q5 のみ空でもクラッシュしない");
  var only6 = clone(MINI); only6.basis_date = "2026-08-01"; only6.queries.q6_conv_actuals.rows = [];
  ok(isFinite(L.buildModel(only6, {}).total.landing.high), "q6 のみ空でもクラッシュしない");
});

describe("ペース入力の未指定は既定値・明示 0 は 0", function () {
  var def = L.defaultPaces(MINI.queries.q6_conv_actuals.rows, 10);   // on 0.6 / kyoten 0.2

  [["null", null], ["undefined", undefined], ["空文字", ""], ["NaN", NaN]].forEach(function (c) {
    var m = L.buildModel(MINI, { paceOn: c[1], paceKyoten: c[1] });
    near(m.inputs.paceOn, def.paceOn, 1e-12, "paceOn = " + c[0] + " → 既定値 " + def.paceOn + " にフォールバック");
    near(m.inputs.paceKyoten, def.paceKyoten, 1e-12, "paceKyoten = " + c[0] + " → 既定値 " + def.paceKyoten);
  });

  // opts ごと省略しても既定値
  var mOmit = L.buildModel(MINI, {});
  near(mOmit.inputs.paceOn, def.paceOn, 1e-12, "opts に paceOn を含めない → 既定値");

  // 明示的な 0 は 0 のまま（③ を見込まないシナリオ）
  var mZero = L.buildModel(MINI, { paceOn: 0, paceKyoten: 0 });
  eq(mZero.inputs.paceOn, 0, "paceOn = 0（明示）→ 0 のまま");
  eq(mZero.inputs.paceKyoten, 0, "paceKyoten = 0（明示）→ 0 のまま");
  near(mZero.lks.components.p3, 0, 1e-9, "明示 0 なら ③ = 0");
  ok(mOmit.lks.components.p3 > 0, "既定値なら ③ > 0（0 と既定が区別できている）");

  // 文字列で来ても数値として扱う（入力欄由来）
  var mStr = L.buildModel(MINI, { paceOn: "3.5", paceKyoten: "0" });
  near(mStr.inputs.paceOn, 3.5, 1e-12, "文字列 \"3.5\" → 3.5");
  eq(mStr.inputs.paceKyoten, 0, "文字列 \"0\" → 0（既定値に戻さない）");

  // 片方だけ 0、片方は既定
  var mMix = L.buildModel(MINI, { paceOn: 0 });
  eq(mMix.inputs.paceOn, 0, "片方だけ明示 0 → 0");
  near(mMix.inputs.paceKyoten, def.paceKyoten, 1e-12, "もう片方は既定値のまま");
});

/* ---------------------------------------------------------------- *
 * 4. 実データ固定の回帰テスト（凍結スナップショット）
 * ---------------------------------------------------------------- *
 * dashboard/data/latest.json は日々更新されうるので、このテストは必ず
 * dashboard/testdata/snapshot-2026-08-25.json（凍結コピー）だけを参照する。
 * 基準値は 2026-08-25 のデータで検証済みの値。丸め誤差 ±1 円まで許容。
 */
describe("回帰: 凍結スナップショット 2026-08-25 の検証済み基準値", function () {
  var FROZEN = path.join(__dirname, "testdata", "snapshot-2026-08-25.json");
  var raw;
  try { raw = fs.readFileSync(FROZEN, "utf8"); }
  catch (e) { skipped("凍結スナップショットが無い: " + FROZEN); return; }

  var data = JSON.parse(raw);
  eq(data.basis_date, "2026-08-25", "凍結スナップショットの基準日");
  eq(data.target_month, "2026-08", "凍結スナップショットの対象月");

  var m = L.buildModel(data, {});   // 既定ペース・既定 lagWeight 0.5
  var YEN = 1;   // 丸め誤差の許容（円）

  [
    ["① 更新残 P1", m.lks.components.p1, 1507220],
    ["② 既成約未計上 P2", m.lks.components.p2, 566607],
    ["③ 今後の成約 P3", m.lks.components.p3, 251826],
    ["④ 滞納・処理ラグ P4", m.lks.components.p4, 6317361],
    ["LKS pending low", m.lks.pending.low, 2325654],
    ["LKS pending central", m.lks.pending.central, 5484334],
    ["LKS pending high", m.lks.pending.high, 8643015],
    ["LKS 着地 central", m.lks.landing.central, 356685140],
    ["MNY ① P1m", m.mny.components.p1, 65886],
    ["MNY ④ P4m", m.mny.components.p4, 78471],
    ["プロデ 着地 central", m.pd.landing.central, 10160505],
    ["プロデ upside", m.pd.upside, 0]
  ].forEach(function (r) {
    near(r[1], r[2], YEN, "固定値 " + r[0] + " = " + r[2].toLocaleString("ja-JP") + " 円（±1円）");
  });

  near(m.inputs.k, 1.0482, 5e-5, "拠点係数 k ≈ 1.0482");
  eq(m.inputs.kSource, "q5", "k は q5 から算出");
  eq(m.inputs.lagWeight, 0.5, "既定の織り込み率は 0.5");

  // 導出関係が壊れていないこと
  near(m.lks.pending.low, m.lks.components.p1 + m.lks.components.p2 + m.lks.components.p3, YEN,
    "low = ① + ② + ③");
  near(m.lks.pending.central, m.lks.pending.low + 0.5 * m.lks.components.p4, YEN,
    "central = low + 0.5 × ④");
  near(m.lks.pending.high, m.lks.pending.low + m.lks.components.p4, YEN, "high = low + ④");
  near(m.lks.landing.central, m.lks.booked + m.lks.pending.central, YEN, "着地 = 計上済み + pending");
  near(m.pd.landing.high, m.pd.landing.central + m.pd.upside, YEN, "プロデ high = central + upside");

  // シナリオを既定に固定した状態のスカラー
  near(m.inputs.paceOn, 27.4, 1e-9, "既定ペース（オンライン）= 27.4 件/日");
  near(m.inputs.paceKyoten, 3.7, 1e-9, "既定ペース（拠点）= 3.7 件/日");
  eq(m.lks.booked, 351200806, "LKS 計上済み");
  eq(m.mny.booked, 4492633, "MNY 計上済み");
  eq(m.pd.booked, 10160505, "プロデ 計上済み");

  // 内蔵チェックが全て通ること
  m.checks.forEach(function (c) { ok(c.ok, "凍結スナップショットで内蔵チェック: " + c.label, c.detail); });

  // ライブ経路（BQ REST → parseBqResult）でも同じ着地になること
  var q1 = data.queries.q1_official_monthly.rows;
  var fields = [
    { name: "year_month", type: "STRING" }, { name: "lks", type: "INTEGER" },
    { name: "mny", type: "INTEGER" }, { name: "pd", type: "INTEGER" }
  ];
  var live = JSON.parse(raw);
  live.queries.q1_official_monthly = {
    rows: L.parseBqResult(JSON.stringify({
      jobComplete: true, schema: { fields: fields },
      rows: q1.map(function (r) {
        return { f: [{ v: r.year_month }, { v: String(r.lks) }, { v: String(r.mny) }, { v: String(r.pd) }] };
      })
    }))
  };
  near(L.buildModel(live, {}).lks.landing.central, 356685140, YEN,
    "ライブ経路でも LKS 着地 central = 356,685,140 円");
});

/* ---------------------------------------------------------------- *
 * 5. フィクスチャに対する契約の不変条件
 * ---------------------------------------------------------------- */
var candidates = [
  process.argv[2],
  process.env.FA_FIXTURE,
  path.join(__dirname, "data", "latest.json"),
  path.join(__dirname, "fixture.json"),
  "/tmp/claude-0/-home-user-triptrip/ba0c624f-4401-5154-a133-43c04e86e8f6/scratchpad/t2_fixture.json"
].filter(Boolean);

var fixturePath = null;
for (var i = 0; i < candidates.length; i++) {
  try { if (fs.statSync(candidates[i]).isFile()) { fixturePath = candidates[i]; break; } } catch (e) { /* next */ }
}

describe("契約の不変条件（フィクスチャ / スナップショット）", function () {
  if (!fixturePath) { skipped("フィクスチャが見つからないため不変条件テストを省略"); return; }
  console.log("  data: " + fixturePath);
  var data = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
  var m = L.buildModel(data, {});

  var q = data.queries || {};
  var q1 = (q.q1_official_monthly || {}).rows || [];
  var q4 = (q.q4_lks_channel_booked || {}).rows || [];
  var q6 = (q.q6_conv_actuals || {}).rows || [];
  var q3 = (q.q3_mny_pd_pending || {}).rows || [];

  // 不変条件 1: Σ q4.booked_fa ≒ q1 当月 lks（±1%）
  var q4sum = q4.reduce(function (s, r) { return s + (Number(r.booked_fa) || 0); }, 0);
  var curLks = m.lks.booked;
  var diff = curLks > 0 ? Math.abs(q4sum - curLks) / curLks : 0;
  ok(diff <= 0.01, "① Σ q4.booked_fa ≒ q1 当月 lks（±1%）", "Σ=" + q4sum + " lks=" + curLks + " 差=" + (diff * 100).toFixed(3) + "%");

  // 不変条件 2: low ≤ central ≤ high、各成分 ≥ 0
  ["lks", "mny", "pd", "total"].forEach(function (kk) {
    var s = m[kk];
    ok(s.landing.low <= s.landing.central + 1e-9 && s.landing.central <= s.landing.high + 1e-9,
      "② " + kk + ": low ≤ central ≤ high",
      s.landing.low + " / " + s.landing.central + " / " + s.landing.high);
  });
  [["①", m.lks.components.p1], ["②", m.lks.components.p2], ["③", m.lks.components.p3], ["④", m.lks.components.p4],
  ["MNY①", m.mny.components.p1], ["MNY④", m.mny.components.p4], ["PD upside", m.pd.upside]].forEach(function (p) {
    ok(p[1] >= 0, "② 成分 " + p[0] + " ≥ 0", String(p[1]));
  });
  m.lks.channels.forEach(function (c) {
    ok(c.pending.low <= c.pending.central + 1e-9 && c.pending.central <= c.pending.high + 1e-9,
      "② チャネル " + c.channel + ": low ≤ central ≤ high");
  });

  // 不変条件 3: q1 の 2026-07 実績
  var jul = q1.filter(function (r) { return r.year_month === "2026-07"; })[0];
  if (jul) {
    eq(Number(jul.lks), 356607091, "③ 2026-07 lks = 356,607,091");
    eq(Number(jul.mny), 4619550, "③ 2026-07 mny = 4,619,550");
    eq(Number(jul.pd), 9080741, "③ 2026-07 pd = 9,080,741");
  } else { skipped("③ q1 に 2026-07 が無い"); }

  // 不変条件 4: MNY の単価が 500〜900 円/日
  ok(m.mny.rate >= 500 && m.mny.rate <= 900, "④ MNY 単価が 500〜900 円/日", String(m.mny.rate));

  // 不変条件 5: Σ q6.booked_fa_valid ≤ q1 当月 lks
  var q6sum = q6.reduce(function (s, r) { return s + (Number(r.booked_fa_valid) || 0); }, 0);
  ok(q6sum <= curLks, "⑤ Σ q6.booked_fa_valid ≤ q1 当月 lks", q6sum + " ≤ " + curLks);

  // 不変条件 6: multicreator の fa_per_day_cur が NULL または 0
  var mc = q3.filter(function (r) { return r.service_key === "multicreator"; })[0];
  if (mc) {
    ok(mc.fa_per_day_cur === null || mc.fa_per_day_cur === undefined || Number(mc.fa_per_day_cur) === 0,
      "⑥ multicreator の fa_per_day_cur が NULL / 0", String(mc.fa_per_day_cur));
  } else { skipped("⑥ q3 に multicreator 行が無い"); }

  // チャネル按分の合計整合
  var sums = ["p1", "p2", "p3", "p4"].map(function (kk) {
    return m.lks.channels.reduce(function (s, c) { return s + c.inc[kk]; }, 0);
  });
  near(sums[0], m.lks.components.p1, 1e-6, "チャネル按分 Σ① = 全体①");
  near(sums[1], m.lks.components.p2, 1e-6, "チャネル按分 Σ② = 全体②");
  near(sums[2], m.lks.components.p3, 1e-6, "チャネル別 Σ③ = 全体③");
  near(sums[3], m.lks.components.p4, 1e-6, "チャネル按分 Σ④ = 全体④");
  var shareSum = m.lks.channels.reduce(function (s, c) { return s + c.share; }, 0);
  near(shareSum, 1, 1e-9, "share の合計 = 1（成約記録なしを除く）");

  // Σ チャネル着地 と LKS 着地 の差は Σq4 と q1 の差ぶんに一致する
  var chLanding = m.lks.channels.reduce(function (s, c) { return s + c.landing.central; }, 0);
  near(chLanding - m.lks.landing.central, q4sum - curLks, 1e-6,
    "Σチャネル着地 − LKS着地 = Σq4 − q1lks（按分の閉じ）");

  // 表示レンジの妥当性（実測レンジのサニティ）
  ok(m.lks.landing.central > 3.0e8 && m.lks.landing.central < 4.5e8, "LKS 着地 central が現実的なレンジ", L.fmtYen(m.lks.landing.central));
  ok(m.total.landing.central >= m.total.booked, "合計着地 ≥ 合計計上済み");

  // モデル内蔵チェックが全部 ok
  m.checks.forEach(function (c) { ok(c.ok, "内蔵チェック: " + c.label, c.detail); });

  // 履歴
  ok(m.history.length >= 13, "履歴が 13 ヶ月以上（12ヶ月前〜未来月）", String(m.history.length));
  ok(m.history.some(function (h) { return h.isFuture; }), "未来月の行がある");
  ok(m.history.filter(function (h) { return h.isCurrent; }).length === 1, "当月の行がちょうど 1 つ");

  // ライブ経路と同値: BQ REST 形状 → parseBqResult → buildModel が一致すること
  var fields = [
    { name: "year_month", type: "STRING" }, { name: "lks", type: "INTEGER" },
    { name: "mny", type: "INTEGER" }, { name: "pd", type: "INTEGER" }
  ];
  var restRows = q1.map(function (r) {
    return { f: [{ v: r.year_month }, { v: String(r.lks) }, { v: String(r.mny) }, { v: String(r.pd) }] };
  });
  var round = L.parseBqResult(JSON.stringify({ jobComplete: true, schema: { fields: fields }, rows: restRows }));
  var live = JSON.parse(JSON.stringify(data));
  live.queries.q1_official_monthly = { rows: round };
  var m2 = L.buildModel(live, {});
  near(m2.total.landing.central, m.total.landing.central, 1e-9, "スナップショット経路とライブ経路が同一結果");
});

/* ---------------------------------------------------------------- *
 * 6. フォーマッタ
 * ---------------------------------------------------------------- */
describe("フォーマッタ", function () {
  eq(L.fmtYen(356607091), "356,607,091", "円 3桁区切り");
  eq(L.fmtYen(-1234), "-1,234", "負の値");
  eq(L.fmtYen(0), "0", "ゼロ");
  eq(L.fmtMillion(356607091), "356.6M", "百万円・小数1桁");
  eq(L.fmtMillion(null), "—", "null は —");
  eq(L.fmtPct(0.0061), "+0.6%", "パーセント（符号付き）");
  eq(L.lastDayOfMonth("2026-02"), 28, "2026-02 の末日 = 28");
  eq(L.lastDayOfMonth("2024-02"), 29, "2024-02 の末日 = 29（閏年）");
  eq(L.prevMonth("2026-01"), "2025-12", "前月（年またぎ）");
});

/* ---------------------------------------------------------------- */
console.log("\n" + "=".repeat(62));
console.log("pass " + pass + " / fail " + fail + " / skip " + skip);
if (fail) {
  console.log("\n失敗:");
  failures.forEach(function (f) { console.log("  - " + f); });
}
console.log("=".repeat(62));
process.exit(fail ? 1 : 0);
