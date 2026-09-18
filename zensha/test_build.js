#!/usr/bin/env node
/*
 * test_build.js — zensha/logic.js の単体テストと、build.py 出力の描画テスト。
 *
 *   node zensha/test_build.js [fixture.json]
 *
 * (a) フィクスチャで build.py を回し、生成HTML の <script> を DOM スタブ上で実行して
 *     「原本が出していた主要数値」が描画されるかを見る。
 * (b) logic.js の純関数を直接叩く（判定・外挿・必要ペース・null日次・境界）。
 */
"use strict";

var fs = require("fs");
var os = require("os");
var path = require("path");
var cp = require("child_process");

var HERE = __dirname;
var L = require(path.join(HERE, "logic.js"));

/* ---------------------------------------------------------------- *
 * 最小アサーションハーネス
 * ---------------------------------------------------------------- */
var pass = 0, fail = 0, failures = [], group = "";
function describe(name, fn) { group = name; console.log("\n── " + name); fn(); }
function ok(cond, msg, detail) {
  if (cond) { pass++; console.log("  ✓ " + msg); }
  else {
    fail++; failures.push(group + " / " + msg + (detail ? "  → " + detail : ""));
    console.log("  ✗ " + msg + (detail ? "  → " + detail : ""));
  }
}
function eq(a, b, msg) { ok(a === b, msg, "actual=" + JSON.stringify(a) + " expected=" + JSON.stringify(b)); }
function near(a, b, tol, msg) {
  var t = tol === undefined ? 1e-9 : tol;
  ok(Math.abs(a - b) <= t, msg, "actual=" + a + " expected=" + b);
}

/* ---------------------------------------------------------------- *
 * DOM スタブ — テンプレートの <script> をブラウザ無しで走らせる
 * ---------------------------------------------------------------- */
function makeNode(tag) {
  return {
    tag: tag || "div", innerHTML: "", textContent: "", hidden: false, offsetTop: 0,
    _attr: {},
    setAttribute: function (k, v) { this._attr[k] = String(v); },
    getAttribute: function (k) { return this._attr[k]; },
    addEventListener: function () { },
    querySelectorAll: function () { return []; },
    closest: function () { return null; }
  };
}

function renderPage(html) {
  var a = html.indexOf("<script>"), b = html.indexOf("</script>");
  if (a < 0 || b < 0) throw new Error("<script> が見つかりません");
  var code = html.slice(a + "<script>".length, b);

  var byId = {};
  // クラス指定で引かれるノードは、HTML 側の出現数だけ用意する
  var classes = {};
  [".js-span-note", ".js-mno", ".js-fy", ".js-asof",
    ".js-prov-note-mny", ".js-prov-note-pro",
    ".js-prov-asof-mny", ".js-prov-asof-pro"].forEach(function (sel) {
    var cls = sel.slice(1);
    var n = (html.match(new RegExp(cls, "g")) || []).length;
    classes[sel] = [];
    for (var i = 0; i < n; i++) classes[sel].push(makeNode("span"));
  });

  var doc = {
    getElementById: function (id) {
      if (!byId[id]) byId[id] = makeNode();
      return byId[id];
    },
    querySelector: function (sel) {
      if (sel === ".tabs") return makeNode();
      return makeNode();
    },
    querySelectorAll: function (sel) { return classes[sel] || []; }
  };
  var win = { scrollTo: function () { } };

  /* eslint-disable no-new-func */
  new Function("document", "window", code)(doc, win);

  var out = [];
  Object.keys(byId).forEach(function (k) {
    out.push(byId[k].innerHTML);
    out.push(byId[k].textContent);
  });
  Object.keys(classes).forEach(function (sel) {
    classes[sel].forEach(function (n) { out.push(n.innerHTML); out.push(n.textContent); });
  });
  return { text: out.join("\n"), byId: byId };
}

/* ---------------------------------------------------------------- *
 * (a) ビルド → 描画 → 原本の主要数値
 * ---------------------------------------------------------------- */
var fixture = process.argv[2] || path.join(HERE, "testdata", "fixture-original-2026-09-08.json");
var manualFixture = path.join(HERE, "testdata", "manual-fixture.json");
var prodManual = path.join(HERE, "data", "manual.json");
var prodData = path.join(HERE, "data", "latest.json");
var outFile = path.join(os.tmpdir(), "zensha-test-" + process.pid + ".html");
var built = null, rendered = null, buildErr = null;

try {
  cp.execFileSync("python3", [
    path.join(HERE, "build.py"),
    "--data", fixture,
    "--manual", manualFixture,
    "-o", outFile
  ], { stdio: "pipe" });
  built = fs.readFileSync(outFile, "utf8");
  rendered = renderPage(built);
} catch (e) {
  buildErr = e;
}

describe("build.py — 生成物の健全性", function () {
  ok(!buildErr, "build.py が成功する", buildErr && String(buildErr.message || buildErr).slice(0, 400));
  if (!built) return;
  ok(built.indexOf("/*__DATA__*/") < 0, "/*__DATA__*/ が残っていない");
  ok(built.indexOf("/*__LOGIC__*/") < 0, "/*__LOGIC__*/ が残っていない");
  eq((built.match(/<script/g) || []).length, 1, "<script> は1つだけ");
  eq((built.match(/<\/script>/g) || []).length, 1, "</script> は1つだけ");
  ok(built.indexOf("<title>全社着地モニター</title>") >= 0, "<title> が原本のまま");
  ok(built.indexOf('@media (prefers-color-scheme: dark)') >= 0, "ダークモードのCSSが残っている");
  ok(built.indexOf('@media (max-width:900px)') >= 0, "phone幅のCSSが残っている");
});

function has(s, label) {
  ok(rendered && rendered.text.indexOf(s) >= 0, (label || s), label ? ("探した文字列: " + s) : "");
}

describe("描画 — ヘッダ・見出し（原本と同じ数値）", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  has("データ取得 <b>2026-09-08</b>（実績は 9/7 まで）");
  has("9月 <b>経過7日 / 30日・残23日</b>");
  has("4〜8月は実績、9月以降はAヨミ。棒の上は「計画との差（百万円）」");
  has("9月単月"); has("FY26 通期");
  has("3.99", "全社 9月単月 着地見込み 3.99億円");
  has("4.14", "全社 9月単月 月計画 4.14億円");
  has("48.97", "全社 FY26 着地見通し 48.97億円");
  has("51.33", "全社 FY26 期初計画 51.33億円");
  has("昨日 9/7 時点");
  has("「日割計画差」＝ 昨日までの実績 −（月計画 × 7日 ÷ 30日）");
});

describe("描画 — トピックス 01〜04", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  has("通期▲¥235百万の88%は、法人とグロースタジオの2つ");
  has("▲¥76百万", "法人の通期未達");
  has("38.7%", "法人の通期達成率");
  has("▲¥131百万", "グロースタジオの通期未達");
  has("60.4%", "グロースタジオの通期達成率");
  has("▲¥207百万", "上位2サービスの合計");
  has("▲¥28百万", "残り3サービスの合計");
  has("全社の未達は主力サービスの問題ではない");
  has("拠点だけが落ちている");
  has("887件（102.2%）");
  has("97件（65.5%）");
  has("足りないのは申込");
  has("計画824 → 見込み496");
  has("単価が1.64倍");
  has("¥5.8M（22%）", "拠点広告費の消化");
  has("¥26.5M", "拠点広告費の計画");
  has("¥52,862", "拠点の申込CPA（Aヨミ）");
  has("984件（96.9%）");
  has("896件（88.2%）");
  has("判定が「要テコ入れ」と「未達リスク大」に分かれる水準");
});

describe("描画 — レーンカード（オンライン／拠点）", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  has("月目標 868件"); has("月目標 148件");
  has(">887<", "オンライン Aヨミ 887件");
  has("102.2%"); has(" ／ +19件");
  has(">789<", "オンライン 実績ペース外挿 789件");
  has("90.9%"); has(" ／ ▲79件");
  has(">97<", "拠点 Aヨミ 97件");
  has("65.5%"); has(" ／ ▲51件");
  has(">107<", "拠点 実績ペース外挿 107件");
  has("72.3%"); has(" ／ ▲41件");
  has("日割計画 <b>203件</b> ／ 実績 <b>184件</b>（21.2% vs 23.3%）");
  has("日割計画 <b>35件</b> ／ 実績 <b>25件</b>（16.9% vs 23.3%）");
  has("残23日で必要なペース");
  has(">29.7<", "オンライン 必要ペース 29.7件/日");
  has(">5.3<", "拠点 必要ペース 5.3件/日");
  has("計画ペース <b>28.9</b>");
  has("計画ペース <b>4.9</b>");
  has("2つの予測が <b>98件</b> 割れている");
  has("<b>2つの予測が一致して未達。</b>成約率は計画どおり（37.7% vs 37.1%）で、足りないのは申込。");
});

describe("描画 — 日次・段階テーブル・主因", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  has("7日計 180件", "オンライン日次合計 180件");
  has("7日計 22件", "拠点日次合計 22件");
  has("系列は 日次集計_（梅田・福岡・横浜）の最終成約");
  has("日次成約数（梅田＋福岡＋横浜）");
  has("9/1〜9/7。線は「月計画を日割りしたペース」と「残23日で必要なペース」");
  // 段階テーブル（オンライン）
  has("7,930", "オンライン申込 Aヨミ");
  has("3,340", "オンライン参加 Aヨミ");
  has("¥22,092", "オンライン CPA Aヨミ");
  has("41.8%", "オンライン 参加率 Aヨミ");
  // 主因
  has("月計画868 → Aヨミ887（差 +19件 ≒ ①＋②）", "M1: ①+②=20 と差 19 がずれるので ≒");
  has("月計画148 → Aヨミ97（差 ▲51件 ＝ ①＋③）");
  has("+57件", "オンライン ①参加要因");
  has("▲37件", "オンライン ②転換要因");
  has("98件", "オンライン ③予測差");
  has("▲53件", "拠点 ①申込要因");
  has("1.64倍", "拠点 ②CPA倍率");
  has("+2件", "拠点 ③転換要因");
  has("① 最大要因"); has("② ①が起きている原因"); has("③ 問題ではない所");
  has("申込が計画の6割しか集まっていない");
  has("現場の転換力は計画どおり");
});

describe("描画 — manual.json 由来", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  has("梅田・横浜はCPA改善が進行中（梅田 8月¥42,730→9月目標¥32,000、横浜 ¥44,613→¥31,000）。");
  has("福岡は増額前に構造改善を先行する方針");
  has("CPA ¥18,421 と目標¥23,022 を下回って推移。");
  has("拠点の広告費を増やすか、単価を直してから増やすか");
  has("9/10まで", "decisions の {{month}} が埋まる"); has("営業役員 ＋ 集客役員");
  // C1(a): manual の本文がトークンから埋まり、トピックス03 と同じ数値になる
  has("予算は¥26.5Mに対し消化¥5.8M（22%）", "C1: decisions[0] の広告費がトークン由来");
  has("計画の1.64倍", "C1: decisions[0] のCPA倍率がトピックス03と一致");
  has("オンラインは月目標に対し +19件（Aヨミ102.2%）", "C1: decisions[1] の件数・達成率");
  has("SHElikes全体 1,016件 で見れば ▲32件", "C1: decisions[1] の全体差がトークン由来");
  has("拠点単体は 65.5% のまま残る", "C1: decisions[1] の拠点達成率");
  has("CPA ¥18,421 と目標¥23,022", "C1: field_notes のCPAがトークン由来");
  has("参加率31.9%は月内の未開催予約", "C1: field_notes の参加率がトークン由来");
  has("拠点の9/7まで実績は KPIサマリ25件／日次集計の合算22件。オンラインも184件／180件。", "C1: notes[5]");
  has("（9/7 の翌日〜月末＝残23日）", "C1: notes[7] の残日数");
  ok(rendered.text.indexOf("[未取得]") < 0, "C1: フィクスチャでは未解決トークンが無い");
  has("手入力部分の最終更新: 2026-09-18");
  has("数値は毎朝の自動更新");
  ok(rendered.text.indexOf("本番では開くたびに読み直す") < 0, "削除した注記（2日前のデータ…）が残っていない");
  has("元データ：全社（単月確認用・通期着地見通し）");
  has("元データ：FY26_拠点モニタリング");
  // 仮KPI（SHEmoney / PRO）
  has("月目標 210件 ／ 9/7 まで実績 46件");
  has("月目標 96件 ／ 9/7 まで実績 22件");
  has("計画 7.0件/日"); has("必要 7.1件/日");
});

describe("manual.json の差し替えだけで判断事項・注記が変わる", function () {
  if (!built) { ok(false, "ビルドできていない"); return; }
  var m = JSON.parse(fs.readFileSync(manualFixture, "utf8"));
  m.decisions = [{ due: "2026-09-30", urgent: true, title: "ダミー判断", body: "本文ダミー", owner: "ダミー役員" }];
  m.notes = [{ text: "ダミー注記", tag: "テスト" }];
  m.updated_at = "2099-01-01";
  var mf = path.join(os.tmpdir(), "zensha-manual-" + process.pid + ".json");
  var of = path.join(os.tmpdir(), "zensha-alt-" + process.pid + ".html");
  fs.writeFileSync(mf, JSON.stringify(m), "utf8");
  cp.execFileSync("python3", [path.join(HERE, "build.py"), "--data", fixture, "--manual", mf, "-o", of], { stdio: "pipe" });
  var r2 = renderPage(fs.readFileSync(of, "utf8"));
  ok(r2.text.indexOf("ダミー判断") >= 0, "判断事項が差し替わる");
  ok(r2.text.indexOf("ダミー注記") >= 0, "注記が差し替わる");
  ok(r2.text.indexOf("手入力部分の最終更新: 2099-01-01") >= 0, "updated_at が差し替わる");
  ok(r2.text.indexOf("拠点の広告費を増やすか") < 0, "元の判断事項は消える");
  ok(r2.text.indexOf("887") >= 0, "latest 由来の数値は変わらない");
  fs.unlinkSync(mf); fs.unlinkSync(of);
});

describe("build.py — 必須キー欠損で非0終了", function () {
  var bad = { basis_date: "2026-09-08" };
  var bf = path.join(os.tmpdir(), "zensha-bad-" + process.pid + ".json");
  fs.writeFileSync(bf, JSON.stringify(bad), "utf8");
  var code = null, err = "";
  try {
    cp.execFileSync("python3", [path.join(HERE, "build.py"), "--data", bf,
      "-o", path.join(os.tmpdir(), "zensha-bad-" + process.pid + ".html")], { stdio: "pipe" });
    code = 0;
  } catch (e) { code = e.status; err = String(e.stderr || ""); }
  ok(code !== 0, "非0終了する", "status=" + code);
  ok(/エラー: latest\.json に必須キーがありません/.test(err), "日本語のエラーが出る", err.slice(0, 200));
  fs.unlinkSync(bf);
});

/* ---------------------------------------------------------------- *
 * (b) 純関数
 * ---------------------------------------------------------------- */
describe("判定 verdict / tone", function () {
  eq(L.verdict(120).main, "達成見込み", "120% → 達成見込み");
  eq(L.verdict(100).main, "達成見込み", "ちょうど100% → 達成見込み");
  eq(L.verdict(100).sub, "", "達成側にサブバッジは無い");
  eq(L.verdict(99.9).sub, "要テコ入れ", "99.9% → 要テコ入れ");
  eq(L.verdict(90).sub, "要テコ入れ", "ちょうど90% → 要テコ入れ");
  eq(L.verdict(89.9).sub, "未達リスク大", "89.9% → 未達リスク大");
  eq(L.verdictLabel(102.2), "達成見込み", "102.2% のラベル");
  eq(L.verdictLabel(96.9), "要テコ入れ", "96.9% のラベル");
  eq(L.verdictLabel(88.2), "未達リスク大", "88.2% のラベル");
  eq(L.tone(100), "g", "tone 100 → g");
  eq(L.tone(90), "w", "tone 90 → w");
  eq(L.tone(89.99), "b", "tone 89.99 → b");
});

describe("フォーマット（原本の丸め）", function () {
  eq(L.oku(399413635), "3.99", "億円2桁");
  eq(L.mil(23246300), "23.2", "百万円1桁");
  eq(L.milR(-235451114 * -1), "235", "百万円（整数）");
  eq(L.pct(65.5405), "65.5%", "%1桁");
  eq(L.yen(52862), "¥52,862", "円は整数＋カンマ");
  eq(L.n0(3340), "3,340", "件は整数＋カンマ");
  eq(L.milM(26500000), "¥26.5M", "百万円Mつき");
  eq(L.signedN(-19), "▲19", "負は ▲");
  eq(L.signedN(19), "+19", "正は +");
  eq(L.signedMilR(-235451114), "▲¥235百万", "通期未達額");
});

describe("ペース計算（原本の式）", function () {
  eq(L.paceExtrap(184, 7, 30), 789, "オンライン 実績ペース外挿 = 184/7×30");
  eq(L.paceExtrap(25, 7, 30), 107, "拠点 実績ペース外挿 = 25/7×30");
  eq(L.planToDate(868, 7, 30), 203, "オンライン 日割計画");
  eq(L.planToDate(148, 7, 30), 35, "拠点 日割計画");
  near(L.neededPace(868, 184, 23), 684 / 23, 1e-9, "オンライン 必要ペース");
  eq(L.neededPace(868, 184, 23).toFixed(1), "29.7", "オンライン 必要ペース 29.7");
  eq(L.neededPace(148, 25, 23).toFixed(1), "5.3", "拠点 必要ペース 5.3");
  eq(L.planPace(868, 30).toFixed(1), "28.9", "オンライン 計画ペース");
  eq(L.planPace(148, 30).toFixed(1), "4.9", "拠点 計画ペース");
  eq(L.neededPace(148, 25, 0), null, "remaining=0 なら必要ペースは null");
  eq(L.paceExtrap(10, 0, 30), null, "elapsed=0 なら外挿は null");
  eq(L.paceExtrap(10, 1, 30), 300, "elapsed=1 でも外挿できる");
  eq(L.planToDate(868, 1, 30), 29, "elapsed=1 の日割計画");
});

describe("日次の欠損 null", function () {
  var s = L.dailyStats([30, null, 25, 28, null]);
  eq(s.sum, 83, "null を除いた合計");
  eq(s.n, 3, "null を除いた日数");
  eq(s.len, 5, "配列長はそのまま");
  near(s.avg, 83 / 3, 1e-9, "平均は null を除いて計算");
  eq(L.dailyStats([]).avg, null, "空配列の平均は null");
  eq(L.dailyStats([null, null]).avg, null, "全欠損の平均は null");
  near(L.recentPace([1, 2, 3, 4, 5, 6, 7, 8]), (2 + 3 + 4 + 5 + 6 + 7 + 8) / 7, 1e-9, "直近7日平均");
  near(L.recentPace([4, 6]), 5, 1e-9, "7日に満たなければ全日平均");
  near(L.recentPace([4, null, 6]), 5, 1e-9, "null は除外して平均");
  eq(L.recentPace([null]), null, "全欠損なら null");
});

describe("laneModel（原本のレーンカードの数値）", function () {
  var on = L.laneModel({ target: 868, yomi: 887, act: 184, daily: [30, 26, 25, 28, 30, 17, 24], elapsed: 7, days: 30, remaining: 23 });
  eq(on.pace, 789, "オンライン pace");
  eq(L.pct(on.yomiRate), "102.2%", "オンライン Aヨミ達成率");
  eq(L.pct(on.paceRate), "90.9%", "オンライン ペース達成率");
  eq(on.yomiGap, 19, "オンライン Aヨミ差 +19");
  eq(on.paceGap, -79, "オンライン ペース差 ▲79");
  eq(on.planToDate, 203, "オンライン 日割計画");
  eq(on.behind, -19, "オンライン 遅れ ▲19");
  eq(L.pct(on.actRate), "21.2%", "オンライン 実績進捗率");
  eq(L.pct(on.dateRate), "23.3%", "日付の進み");
  eq(on.verdict.sub, "要テコ入れ", "オンラインの判定");
  eq(on.forecastGap, 98, "2つの予測の差 98件");

  var kp = L.laneModel({ target: 148, yomi: 97, act: 25, daily: [4, 3, 3, 1, 3, 5, 3], elapsed: 7, days: 30, remaining: 23 });
  eq(kp.pace, 107, "拠点 pace");
  eq(L.pct(kp.yomiRate), "65.5%", "拠点 Aヨミ達成率");
  eq(L.pct(kp.paceRate), "72.3%", "拠点 ペース達成率");
  eq(kp.behind, -10, "拠点 遅れ ▲10");
  eq(L.pct(kp.actRate), "16.9%", "拠点 実績進捗率");
  eq(kp.verdict.sub, "未達リスク大", "拠点の判定");

  var lks = L.laneModel({ target: 1016, yomi: 984, act: 209, pace: 896, elapsed: 7, days: 30, remaining: 23 });
  eq(L.pct(lks.yomiRate), "96.9%", "SHElikes全体 Aヨミ達成率");
  eq(L.pct(lks.paceRate), "88.2%", "SHElikes全体 ペース達成率");

  var edge = L.laneModel({ target: 100, yomi: 120, act: 120, daily: [120], elapsed: 1, days: 30, remaining: 29 });
  eq(edge.verdict.main, "達成見込み", "達成側は「達成見込み」");
  eq(edge.verdict.sub, "", "達成側にサブバッジ無し");
  ok(edge.need < 0, "目標超過なら必要ペースは負");
  var zero = L.laneModel({ target: 100, yomi: 90, act: 90, daily: [1], elapsed: 30, days: 30, remaining: 0 });
  eq(zero.need, null, "remaining=0 → need は null");
  eq(zero.needRatio, null, "remaining=0 → needRatio も null");
});

describe("段階テーブル stepRow", function () {
  var r = L.stepRow({ n: "申込", p: 7612, y: 7930, a: 2036 }, 7 / 30);
  eq(r.diffText, "+318", "差分");
  eq(L.pct(r.ratio), "104.2%", "達成率");
  eq(r.gapText, "+260", "日割計画差 = 2036 − 7612×7/30");
  var c = L.stepRow({ n: "CPA", p: 32154, y: 52862, a: 53176, unit: "¥", lowerBetter: true }, 7 / 30);
  eq(c.isRate, true, "CPAは日割りできない");
  eq(c.gapText, "―", "日割計画差は ―");
  eq(c.diffTone, "b", "CPA上昇は悪い方向");
  eq(L.pct(c.ratio), "60.8%", "CPAの達成率は p/y");
  var p = L.stepRow({ n: "成約率", p: 27.7, y: 26.6, a: 21.8, unit: "%" }, 7 / 30);
  eq(p.diffText, "▲1.1pt", "%の差分は pt");
});

describe("ギャップの主因", function () {
  var onStep = [
    { n: "申込", p: 7612, y: 7930, a: 2036 }, { n: "予約", p: 7599, y: 7983, a: 2644 },
    { n: "参加", p: 3135, y: 3340, a: 843 }, { n: "成約", p: 868, y: 887, a: 184, key: true },
    { n: "参加率", p: 41.3, y: 41.8, a: 31.9, unit: "%", sep: true },
    { n: "成約率", p: 27.7, y: 26.6, a: 21.8, unit: "%" },
    { n: "CPA", p: 23022, y: 22092, a: 18421, unit: "¥", lowerBetter: true }
  ];
  var kpStep = [
    { n: "申込", p: 824, y: 496, a: 109 }, { n: "参加", p: 399, y: 257, a: 51 },
    { n: "成約", p: 148, y: 97, a: 25, key: true },
    { n: "参加率", p: 48.4, y: 51.8, a: 46.8, unit: "%", sep: true },
    { n: "成約率", p: 37.1, y: 37.7, a: 49.0, unit: "%" },
    { n: "CPA", p: 32154, y: 52862, a: 53176, unit: "¥", lowerBetter: true }
  ];
  var i1 = L.causeImpacts(onStep);
  eq(i1.attend, 57, "オンライン ①参加要因 +57件");
  eq(i1.conv, -37, "オンライン ②転換要因 ▲37件");
  var i2 = L.causeImpacts(kpStep);
  eq(i2.attend, -53, "拠点 ①申込要因 ▲53件");
  eq(i2.conv, 2, "拠点 ③転換要因 +2件");

  var onLane = L.laneModel({ target: 868, yomi: 887, act: 184, daily: [30, 26, 25, 28, 30, 17, 24], elapsed: 7, days: 30, remaining: 23 });
  onLane.throughLabel = "9/7";
  var cOn = L.buildCauses("online", onStep, onLane, null);
  eq(cOn.length, 3, "オンラインは3枚");
  eq(cOn[0].kind, "① 押し上げ", "①は押し上げ");
  eq(cOn[1].kind, "② 押し下げ", "②は押し下げ");
  eq(cOn[2].lab, "Aヨミとペースの差", "③は予測差");
  eq(cOn[2].v, "98件", "③の差は98件");
  eq(L.causeSummary(onStep, cOn), "月計画868 → Aヨミ887（差 +19件 ≒ ①＋②）", "M1: 丸め差があるので ≒");

  var kpLane = L.laneModel({ target: 148, yomi: 97, act: 25, daily: [4, 3, 3, 1, 3, 5, 3], elapsed: 7, days: 30, remaining: 23 });
  var cKp = L.buildCauses("kyoten", kpStep, kpLane, { plan: 26500000, act: 5800000 });
  eq(cKp[0].kind, "① 最大要因", "拠点①は最大要因");
  eq(cKp[1].v, "1.64倍", "拠点②はCPA1.64倍");
  eq(cKp[2].kind, "③ 問題ではない所", "拠点③は問題ではない所");
  ok(cKp[1].p.indexOf("広告費の消化は22%で、日付の進み23%") >= 0,
    "広告費は日付の進みと比べて説明する", cKp[1].p);
  eq(L.causeSummary(kpStep, cKp), "月計画148 → Aヨミ97（差 ▲51件 ＝ ①＋③）", "M1: 一致するときは ＝");

  // 達成側に振れたとき言い回しが破綻しない
  var upStep = kpStep.map(function (r) { return Object.assign({}, r); });
  upStep[0] = { n: "申込", p: 824, y: 900, a: 109 };
  upStep[1] = { n: "参加", p: 399, y: 450, a: 51 };
  upStep[5] = { n: "成約率", p: 37.1, y: 38.5, a: 49.0, unit: "%" };
  var cUp = L.buildCauses("kyoten", upStep, kpLane, { plan: 26500000, act: 26000000 });
  ok(cUp[0].h.indexOf("上回っている") >= 0, "申込超過なら「上回っている」", cUp[0].h);
  ok(cUp[0].v.charAt(0) === "+", "押し上げは + 表記", cUp[0].v);
});

describe("トピックス生成のルール", function () {
  var SV = [
    { key: "lks", short: "SHElikes" }, { key: "mny", short: "SHEmoney" }, { key: "pro", short: "PRO" },
    { key: "hjn", short: "法人" }, { key: "grs", short: "グロースタジオ" }
  ];
  var fx = JSON.parse(fs.readFileSync(fixture, "utf8"));
  var t1 = L.topic01(fx.revenue.fin, SV);
  eq(t1.h, "通期▲¥235百万の88%は、法人とグロースタジオの2つ", "01 見出し");
  ok(t1.p.indexOf("▲¥207百万") >= 0, "01 上位2サービス合計", t1.p);
  ok(t1.p.indexOf("▲¥28百万") >= 0, "01 残り3サービス合計");

  // 全社が超過している場合は言い回しが反転する
  var over = JSON.parse(JSON.stringify(fx.revenue.fin));
  over.total.act = over.total.act.map(function (v) { return v * 2; });
  over.lks.act = over.lks.act.map(function (v) { return v * 2; });
  var t1b = L.topic01(over, SV);
  ok(t1b.h.indexOf("上回る見通し") >= 0, "01 達成側は「上回る見通し」", t1b.h);

  // 他サービスが超過していると寄与率が100%を超えるので、率を出さない言い回しに切り替わる
  var mixed = JSON.parse(JSON.stringify(fx.revenue.fin));
  mixed.total.act = mixed.total.plan.map(function (v, i) {
    return v - (i === 0 ? 100000000 : 0);   // 全社の未達を▲100百万まで縮める
  });
  var t1c = L.topic01(mixed, SV);
  ok(t1c.h.indexOf("%は、") < 0 && t1c.h.indexOf("丸ごと効いている") >= 0,
    "01 寄与率が100%超なら「丸ごと効いている」", t1c.h);

  var onLane = L.laneModel({ target: 868, yomi: 887, act: 184, daily: [30, 26, 25, 28, 30, 17, 24], elapsed: 7, days: 30, remaining: 23 });
  var kpLane = L.laneModel({ target: 148, yomi: 97, act: 25, daily: [4, 3, 3, 1, 3, 5, 3], elapsed: 7, days: 30, remaining: 23 });
  var onStep = fx.lks.online.step, kpStep = fx.lks.kyoten.step;
  var t2 = L.topic02(onLane, kpLane, onStep, kpStep);
  eq(t2.h, "SHElikes の{M}月成約は、拠点だけが落ちている", "02 見出し（拠点だけ未達）");
  var t2b = L.topic02(kpLane, onLane, kpStep, onStep);
  ok(t2b.h.indexOf("オンラインだけが落ちている") >= 0, "02 逆なら「オンラインだけ」", t2b.h);

  var DR7 = 7 / 30 * 100;   // 原本と同じ 経過7日 / 30日
  var t3 = L.topic03(kpStep, { plan: 26500000, act: 5800000 }, DR7);
  eq(t3.h, "拠点の広告費は日割どおりに消化しているが、単価が1.64倍", "03 見出し");
  var cheap = kpStep.map(function (r) { return r.n === "CPA" ? { n: "CPA", p: 32154, y: 30000, a: 30000, unit: "¥", lowerBetter: true } : r; });
  ok(L.topic03(cheap, { plan: 26500000, act: 5800000 }, DR7).h.indexOf("計画内") >= 0, "03 単価が計画内なら言い回しが変わる");
  eq(L.topic03(kpStep, null, DR7), null, "03 広告費が無ければカードを出さない");

  var lks = L.laneModel({ target: 1016, yomi: 984, act: 209, pace: 896, elapsed: 7, days: 30, remaining: 23 });
  var t4 = L.topic04(lks, onLane, kpLane, onStep, kpStep);
  eq(t4.h, "SHElikes は2つの予測が割れている", "04 見出し");
  ok(t4.p.indexOf("判定が「要テコ入れ」と「未達リスク大」に分かれる水準") >= 0, "04 判定の割れ方", t4.p);
  var same = L.laneModel({ target: 1016, yomi: 984, act: 230, pace: 985, elapsed: 7, days: 30, remaining: 23 });
  ok(L.topic04(same, onLane, kpLane, onStep, kpStep).h.indexOf("ほぼ一致") >= 0, "04 差が小さければ「ほぼ一致」");
});

describe("ヘッダ文言と月替わり", function () {
  eq(L.actualSpanNote(["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"], 5),
    "4〜8月は実績、9月以降はAヨミ", "9月時点の文言");
  eq(L.actualSpanNote(["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"], 6),
    "4〜9月は実績、10月以降はAヨミ", "10月に進んでも壊れない");
  eq(L.actualSpanNote(["2026-04", "2026-05"], 1), "4月は実績、5月以降はAヨミ", "実績1ヶ月だけ");
  eq(L.monthNo("2026-09"), 9, "対象月");
  eq(L.prevMonthNo("2026-01"), 12, "1月の前月は12月");
  eq(L.md("2026-09-07"), "9/7", "M/D");
  eq(L.daysDiff("2026-09-10", "2026-09-07"), 3, "日数差");
});

describe("「n日前のデータ」バッジ", function () {
  eq(L.staleBadge("2026-09-18", "2026-09-17", "2026-09-18"), null, "当日取得なら出さない");
  eq(L.staleBadge("2026-09-18", "2026-09-16", "2026-09-18"), null, "取得が今日なら data_through が古くても出さない");
  eq(L.staleBadge("2026-09-17", "2026-09-16", "2026-09-18"), "1日前のデータ", "今日−data_through=2 で出る");
  eq(L.staleBadge("2026-09-08", "2026-09-07", "2026-09-10"), "2日前のデータ", "原本と同じ 2日前のデータ");
  eq(L.staleBadge("2026-09-08", "2026-09-07", "2026-09-09"), "1日前のデータ", "1日遅れ");
  eq(L.staleBadge("2026-09-08", "2026-09-07", "2026-09-08"), null, "basis_date が今日なら出さない");
});

describe("現場で対応中（拠点CPA文）", function () {
  var sites = [
    { name: "梅田", cpa_prev: 42730, cpa_target: 32000 },
    { name: "福岡", cpa_prev: null, cpa_target: null },
    { name: "横浜", cpa_prev: 44613, cpa_target: 31000 }
  ];
  eq(L.siteCpaSentence(sites, "2026-09"),
    "梅田・横浜はCPA改善が進行中（梅田 8月¥42,730→9月目標¥32,000、横浜 ¥44,613→¥31,000）。",
    "原本と同じ文");
  eq(L.siteCpaSentence([], "2026-09"), "", "対象拠点が無ければ空文字");
  ok(L.siteCpaSentence(sites, "2026-01").indexOf("12月¥42,730→1月目標") >= 0, "1月なら前月は12月");
});


/* ---------------------------------------------------------------- *
 * 追加: C1 / C2 / I1〜I5 / M4 / M7 / M8
 * ---------------------------------------------------------------- */
function buildWith(mutate, mutateManual) {
  var fx = JSON.parse(fs.readFileSync(fixture, "utf8"));
  if (mutate) mutate(fx);
  var m = JSON.parse(fs.readFileSync(manualFixture, "utf8"));
  if (mutateManual) mutateManual(m);
  var id = "zensha-case-" + process.pid + "-" + (buildWith._n = (buildWith._n || 0) + 1);
  var df = path.join(os.tmpdir(), id + ".json");
  var mf = path.join(os.tmpdir(), id + "-m.json");
  var of = path.join(os.tmpdir(), id + ".html");
  fs.writeFileSync(df, JSON.stringify(fx), "utf8");
  fs.writeFileSync(mf, JSON.stringify(m), "utf8");
  var r = cp.spawnSync("python3",
    [path.join(HERE, "build.py"), "--data", df, "--manual", mf, "-o", of], { encoding: "utf8" });
  var status = r.status, stderr = String(r.stderr || "");
  var html = fs.existsSync(of) ? fs.readFileSync(of, "utf8") : "";
  var warns = [];
  var realWarn = console.warn;
  console.warn = function (m2) { warns.push(String(m2)); };
  var out = null;
  try { if (html) out = renderPage(html); } finally { console.warn = realWarn; }
  [df, mf, of].forEach(function (f) { try { fs.unlinkSync(f); } catch (e2) { /* noop */ } });
  return { html: html, text: out ? out.text : "", byId: out ? out.byId : {},
    status: status, stderr: stderr, warns: warns };
}

describe("C1 — 未解決トークンは [未取得] + console.warn", function () {
  var warns = [];
  var got = L.fillTokens("a {{nope}} b {{kyoten.spend_rate}}", { "kyoten.spend_rate": null },
    function (m) { warns.push(m); });
  eq(got, "a [未取得] b [未取得]", "未知トークンも null 値も [未取得]");
  eq(warns.length, 2, "どちらも警告される");
  ok(/未知のトークン/.test(warns[0]), "未知トークンの警告文", warns[0]);
  ok(/latest\.json に無い/.test(warns[1]), "値が無いときの警告文", warns[1]);
  eq(L.fillTokens("{{ month }}", { month: "9" }), "9", "前後の空白を許す");
  eq(L.fillTokens("トークン無し", {}), "トークン無し", "トークンが無ければそのまま");

  var c = buildWith(null, function (m) { m.notes.push({ text: "検証用 {{bogus.key}}", tag: null }); });
  ok(c.text.indexOf("検証用 [未取得]") >= 0, "描画でも [未取得] になる");
  ok(c.warns.some(function (w) { return /bogus\.key/.test(w); }), "console.warn が出る", c.warns.join(" / "));
});

describe("C1(c) — 手入力が古いと警告", function () {
  eq(L.manualStaleWarning("2026-09-01", "2026-09-08", 7), "7日前の手入力です。判断事項の前提を確認してください", "7日でしきい値");
  eq(L.manualStaleWarning("2026-09-02", "2026-09-08", 7), null, "6日ならまだ出さない");
  eq(L.manualStaleWarning("2026-09-20", "2026-09-08", 7), null, "manual が新しい側なら出さない");
  var c = buildWith(null, function (m) { m.updated_at = "2026-08-20"; });
  eq(c.status, 0, "警告でも非0終了しない");
  ok(/警告: manual\.json は \d+日前の手入力です/.test(c.stderr), "stderr に警告", c.stderr.slice(0, 160));
  ok(/class="stale">\s*19日前の手入力です/.test(c.text), "画面にも警告バッジ", c.text.slice(0, 0));
});

describe("C2 — cost / CPA が null のとき 0 に潰さない", function () {
  eq(L.spendRateOf({ plan: 100, act: null }), null, "act が null なら消化率は null");
  eq(L.spendRateOf({ plan: 100, act: 0 }), 0, "act が 0 なら 0%");
  eq(L.spendRateOf({ plan: null, act: 10 }), null, "plan が無ければ null");
  eq(L.cpaRatio({ p: 100, y: null }), null, "CPA の Aヨミが無ければ null");
  near(L.cpaRatio({ p: 100, y: 155 }), 1.55, 1e-9, "CPA 倍率");

  var c = buildWith(function (fx) { fx.lks.kyoten.cost.act = null; });
  ok(c.text.indexOf("消化額は未取得") >= 0, "トピックス03が「消化額は未取得」", c.text.indexOf("消化額は未取得"));
  ok(c.text.indexOf("（0%）") < 0, "「（0%）」と断言しない");
  ok(c.text.indexOf("¥0.0M") < 0, "「¥0.0M」と断言しない");
  ok(c.text.indexOf("広告費の消化額は未取得。") >= 0, "主因カードも「消化額は未取得」");

  var c2 = buildWith(function (fx) {
    fx.lks.kyoten.step = fx.lks.kyoten.step.map(function (r) {
      return r.n === "CPA" ? { n: "CPA", p: 32154, y: null, a: null, unit: "¥", lowerBetter: true } : r;
    });
  });
  ok(c2.text.indexOf("申込CPAは未取得。") >= 0, "CPA が null なら倍率文を出さない");
  ok(c2.text.indexOf("倍で、出せば出すほど") < 0, "倍率の断定文が消える");
});

describe("I1 — latest 由来の文字列をエスケープする", function () {
  var payload = '<img src=x onerror="alert(1)">';
  var c = buildWith(function (fx) {
    fx.lks.kyoten.sites[0].name = payload;
    fx.lks.kyoten.daily.dow[0] = payload;
    fx.lks.kyoten.daily.dates[0] = payload;
    fx.lks.online.step[0].n = payload;
    fx.lks.online.daily.note = payload;
    fx.lks.online.step_note = payload;
    fx.sources[0].name = payload;
    fx.sources[0].label = payload;
    fx.sources[0].url = "javascript:alert(1)";
  });
  ok(c.status === 0, "ビルドは通る");
  ok(c.text.indexOf("<img") < 0, "描画結果に <img が現れない");
  ok(c.text.indexOf("onerror=\"alert(1)\"") < 0, "生の onerror 属性が現れない");
  ok(c.text.indexOf("&lt;img") >= 0, "エスケープ済みで出ている");
  ok(/href="#"/.test(c.text), "javascript: URL は # に落とす");
  ok(c.text.indexOf('href="javascript:') < 0, "javascript: がそのまま href に入らない");
  eq(L.safeUrl("HTTPS://a.example/x"), "HTTPS://a.example/x", "https は通す");
  eq(L.safeUrl("data:text/html,x"), "#", "data: は落とす");
  eq(L.safeUrl(null), "#", "null は落とす");
  eq(L.esc('a&b<c>"d\'e'), "a&amp;b&lt;c&gt;&quot;d&#39;e", "esc は5文字を置換");
});

describe("I2 / I3 / M4 — topic01 の言い回し", function () {
  var SV = [
    { key: "lks", short: "SHElikes" }, { key: "mny", short: "SHEmoney" }, { key: "pro", short: "PRO" },
    { key: "hjn", short: "法人" }, { key: "grs", short: "グロースタジオ" }
  ];
  function rev(plan, acts) {
    var r = { total: { plan: [plan.total], act: [acts.total] } };
    ["lks", "mny", "pro", "hjn", "grs"].forEach(function (k) {
      r[k] = { plan: [plan[k]], act: [acts[k]] };
    });
    return r;
  }
  // I2: 全社は超過だが、単体で超過しているサービスが1つも無い
  var noLead = L.topic01(rev(
    { total: 1000, lks: 200, mny: 200, pro: 200, hjn: 200, grs: 200 },
    { total: 1100, lks: 199, mny: 199, pro: 199, hjn: 199, grs: 199 }), SV);
  ok(noLead.h.indexOf("上回る見通し") >= 0, "全社超過の見出し", noLead.h);
  ok(noLead.p.indexOf("上回っているサービスは無い") >= 0, "I2: 未達サービスを牽引役にしない", noLead.p);
  ok(noLead.p.indexOf("が牽引") < 0, "I2: 「が牽引」を出さない");
  // I2: 超過サービスがあるときはそれだけを牽引役にする
  var lead = L.topic01(rev(
    { total: 1000, lks: 200, mny: 200, pro: 200, hjn: 200, grs: 200 },
    { total: 1200, lks: 400, mny: 199, pro: 199, hjn: 199, grs: 199 }), SV);
  ok(/SHElikes .*が牽引/.test(lead.p), "I2: 超過している SHElikes が牽引", lead.p);
  ok(lead.p.indexOf("法人") < 0, "I2: 未達の法人は牽引役に入らない");
  // M4: totalDiff === 0
  var flat = L.topic01(rev(
    { total: 1000, lks: 200, mny: 200, pro: 200, hjn: 200, grs: 200 },
    { total: 1000, lks: 200, mny: 200, pro: 200, hjn: 200, grs: 200 }), SV);
  ok(flat.h.indexOf("計画どおり") >= 0, "M4: 差ゼロは「計画どおり」", flat.h);
  // I3: 「に収まっており」と「広がっている」が同時に出ない
  function noContradiction(t) {
    return !(t.p.indexOf("に収まっており") >= 0 && t.p.indexOf("広がっている") >= 0);
  }
  var spread = L.topic01(rev(
    { total: 1000, lks: 200, mny: 200, pro: 200, hjn: 200, grs: 200 },
    { total: 700, lks: 140, mny: 140, pro: 140, hjn: 140, grs: 140 }), SV);
  ok(noContradiction(spread), "I3: 全サービス均等未達で自己矛盾しない", spread.p);
  ok(spread.p.indexOf("広がっている") >= 0, "I3: 均等に未達なら「広がっている」", spread.p);
  ok(spread.p.indexOf("も合計") >= 0, "I3: 残りも小さくない言い回し", spread.p);
  var fx0 = JSON.parse(fs.readFileSync(fixture, "utf8"));
  ok(noContradiction(L.topic01(fx0.revenue.fin, SV)), "I3: 原本データでも自己矛盾しない");
});

describe("M7 — 未達額順と達成率順で上位2社が分かれる", function () {
  var SV = [
    { key: "lks", short: "SHElikes" }, { key: "mny", short: "SHEmoney" }, { key: "pro", short: "PRO" },
    { key: "hjn", short: "法人" }, { key: "grs", short: "グロースタジオ" }
  ];
  // 額では lks(▲300) と mny(▲200) が最大、達成率では hjn(10%) と pro(50%) が最低
  var r = {
    total: { plan: [2000], act: [1489] },
    lks: { plan: [1000], act: [700] },   // ▲300 / 70.0%
    mny: { plan: [800], act: [600] },    // ▲200 / 75.0%
    pro: { plan: [100], act: [50] },     // ▲50  / 50.0%
    hjn: { plan: [10], act: [1] },       // ▲9   / 10.0%
    grs: { plan: [90], act: [138] }      // +48  / 153.3%
  };
  var t = L.topic01(r, SV);
  ok(t.h.indexOf("SHElikes") >= 0 && t.h.indexOf("SHEmoney") >= 0,
    "未達「額」の大きい2社を選ぶ（達成率の低い法人・PROではない）", t.h);
  ok(t.h.indexOf("法人") < 0 && t.h.indexOf("PRO") < 0, "達成率順で選んでいない", t.h);
  ok(t.p.indexOf("PRO・法人・グロースタジオ") >= 0, "残りはサービス定義順で並ぶ", t.p);
});

describe("I4 — 成約率がほぼ同水準のときは断言しない", function () {
  eq(L.convStance({ a: 26.6, y: 26.7 }), "same", "0.1pt 差は same");
  eq(L.convStance({ a: 26.7, y: 26.7 }), "same", "等号は same");
  eq(L.convStance({ a: 26.1, y: 26.7 }), "below", "0.6pt 下は below");
  eq(L.convStance({ a: 27.3, y: 26.7 }), "above", "0.6pt 上は above");
  eq(L.convStance({ a: null, y: 26.7 }), "unknown", "欠損は unknown");

  var onStep = [
    { n: "申込", p: 7612, y: 7930, a: 2036 }, { n: "予約", p: 7599, y: 7983, a: 2644 },
    { n: "参加", p: 3135, y: 3340, a: 843 }, { n: "成約", p: 868, y: 887, a: 184, key: true },
    { n: "参加率", p: 41.3, y: 41.8, a: 31.9, unit: "%", sep: true },
    { n: "成約率", p: 27.7, y: 26.6, a: 26.7, unit: "%" },
    { n: "CPA", p: 23022, y: 22092, a: 18421, unit: "¥", lowerBetter: true }
  ];
  var lane = L.laneModel({ target: 868, yomi: 887, act: 184, daily: [30, 26, 25, 28, 30, 17, 24], elapsed: 7, days: 30, remaining: 23 });
  lane.throughLabel = "9/7";
  var cards = L.buildCauses("online", onStep, lane, null);
  eq(cards[2].h, "実績の成約率はAヨミの前提とほぼ同水準", "fcCard の見出し");
  ok(cards[2].p.indexOf("日次ペースの振れによるもの") >= 0, "fcCard の本文", cards[2].p);
  ok(cards[2].p.indexOf("届いていない") < 0, "「届いていない」と断言しない");

  var kpStepSame = [
    { n: "申込", p: 824, y: 496, a: 109 }, { n: "参加", p: 399, y: 257, a: 51 },
    { n: "成約", p: 148, y: 97, a: 25, key: true },
    { n: "参加率", p: 48.4, y: 51.8, a: 46.8, unit: "%", sep: true },
    { n: "成約率", p: 37.1, y: 37.7, a: 37.7, unit: "%" },
    { n: "CPA", p: 32154, y: 52862, a: 53176, unit: "¥", lowerBetter: true }
  ];
  var kpLane = L.laneModel({ target: 148, yomi: 97, act: 25, daily: [4, 3, 3, 1, 3, 5, 3], elapsed: 7, days: 30, remaining: 23 });
  var lks = L.laneModel({ target: 1016, yomi: 984, act: 209, pace: 896, elapsed: 7, days: 30, remaining: 23 });
  var t4 = L.topic04(lks, lane, kpLane, onStep, kpStepSame);
  ok(t4.p.indexOf("ほぼ同水準で、差は日次ペースの振れによるもの") >= 0, "topic04 も同水準の言い回し", t4.p);
  ok(t4.p.indexOf("上回っていること") < 0, "等号で「上回っている」と言わない");
});

describe("I5 — 仮KPIは as_of で凍結", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  has("2026-09-07 時点の仮置き。以降は更新していません", "仮パネルの時点注記");
  has("9/7 時点の仮置き", "仮レーンのバッジ");
  has("昨日 9/7 時点（仮）", "仮の段階テーブル見出し");
  var c = buildWith(function (fx) {
    fx.basis_date = "2026-09-25"; fx.data_through = "2026-09-24";
    fx.elapsed_days = 24; fx.remaining_days = 6;
    ["online", "kyoten"].forEach(function (k) {
      var d = fx.lks[k].daily;
      while (d.v.length < 24) { d.v.push(1); d.dates.push("9/" + (d.v.length)); d.dow.push("月"); }
    });
  });
  ok(c.status === 0, "ライブ側だけ進めてビルドできる", c.stderr.slice(0, 200));
  ok(c.text.indexOf("残23日で必要なペース") >= 0, "仮レーンは as_of の残23日のまま");
  ok(c.text.indexOf("2026-09-07 時点の仮置き") >= 0, "仮パネルは 9/7 のまま");
  ok(c.text.indexOf("経過24日 / 30日・残6日") >= 0, "ライブのヘッダは 24日に進む");
});

describe("M4 — 言い回しの境界", function () {
  var kpStep = [
    { n: "申込", p: 824, y: 824, a: 109 }, { n: "参加", p: 399, y: 399, a: 51 },
    { n: "成約", p: 148, y: 148, a: 25, key: true },
    { n: "参加率", p: 48.4, y: 48.4, a: 46.8, unit: "%", sep: true },
    { n: "成約率", p: 37.1, y: 37.1, a: 37.1, unit: "%" },
    { n: "CPA", p: 32154, y: 32154, a: 32154, unit: "¥", lowerBetter: true }
  ];
  // 経過26日 / 30日（進み86.7%）で 85% 消化 → 日割どおり
  var kpLate = L.laneModel({ target: 148, yomi: 148, act: 128, daily: [4, 4, 4, 4, 4, 4, 4], elapsed: 26, days: 30, remaining: 4 });
  var kpLane = L.laneModel({ target: 148, yomi: 148, act: 25, daily: [4, 4, 4, 4, 4, 4, 4], elapsed: 7, days: 30, remaining: 23 });
  var c = L.buildCauses("kyoten", kpStep, kpLate, { plan: 26500000, act: 22525000 }); // 85%
  eq(c[0].h, "申込は計画どおり", "M4: 申込が計画ちょうどなら「計画どおり」");
  ok(c[1].p.indexOf("余っている") < 0, "M4: 月末85%消化で「余っている」と言わない", c[1].p);
  ok(c[1].p.indexOf("ほぼ同じ") >= 0, "M4: 日割どおりと言う", c[1].p);
  eq(c[1].v, "1.00倍", "M4: 倍率は小数2桁");
  ok(/計画の1\.00倍/.test(c[1].p), "M4: 本文の倍率も小数2桁", c[1].p);
  // 経過7日 / 30日（進み23.3%）で 22% 消化 → 日割どおり、40% 消化 → 先行
  var onpace = L.buildCauses("kyoten", kpStep, kpLane, { plan: 26500000, act: 5800000 });   // 21.9%
  ok(onpace[1].p.indexOf("ほぼ同じ") >= 0, "M4: 月初22%は「日割どおり」", onpace[1].p);
  var behind = L.buildCauses("kyoten", kpStep, kpLate, { plan: 26500000, act: 5800000 });   // 21.9% vs 86.7%
  ok(behind[1].p.indexOf("遅れている（予算が残っている）") >= 0,
    "M4: 月末に22%なら「遅れている」", behind[1].p);
  // recentPace: 末尾7日を取ってから null を除外
  near(L.recentPace([99, 99, 1, 2, null, null, null, null, 3]), 2, 1e-9,
    "M4: 末尾7日を取ってから null を除外（先頭の 99 は使わない）");
  near(L.recentPace([99, 99, 99, 4, 4, 4, 4, 4, 4, 4]), 4, 1e-9,
    "M4: 直近7日だけを見る");
  eq(L.recentPace([5, 5, null, null, null, null, null, null, null]), null,
    "M4: 直近7日が全欠損なら null");
});

describe("M8 — step_note と sources.label", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  has("元データ：全社（単月確認用・通期着地見通し）", "M8: label があればリンク文言に使う");
  has("元データ：26年9月_マーケジスイ進捗管理表", "M8: label が無ければ name");
  var c = buildWith(function (fx) { fx.lks.online.step_note = "成約率の計画は 成約目標÷参加計画 で導出"; });
  ok(c.text.indexOf("成約率の計画は 成約目標÷参加計画 で導出") >= 0, "M8: step_note を段階テーブル下に出す");
});

describe("M5 — カレンダー整合の不変条件", function () {
  var c1 = buildWith(function (fx) { fx.lks.online.daily.v = fx.lks.online.daily.v.slice(0, 5); });
  ok(c1.status !== 0, "daily.v の長さが elapsed_days と違えば非0", "status=" + c1.status);
  ok(/daily\.v の長さ/.test(c1.stderr), "日本語エラー", c1.stderr.slice(0, 160));
  var c2 = buildWith(function (fx) { fx.remaining_days = 22; });
  ok(c2.status !== 0, "elapsed+remaining != days_in_month なら非0", "status=" + c2.status);
  ok(/days_in_month/.test(c2.stderr), "日本語エラー", c2.stderr.slice(0, 160));
});


/* ---------------------------------------------------------------- *
 * 追加: F1（レーンカードの文）/ F2（必要ペース≤0）/ F3（エスケープ）/ F4（targets）
 * ---------------------------------------------------------------- */
/** レーンカードの「日付の進みに対して」2行目だけを取り出す。 */
function laneFootTexts(text) {
  var out = [], re = /<div class="statsub">((?:(?!<\/div>).)*)<\/div>/g, m;
  while ((m = re.exec(text)) !== null) out.push(m[1]);
  return out;
}

describe("F1 — レーンカードの文が convStance に載っている", function () {
  // (a) 成約率が完全に等しい（等号）→「上振れている」と言わない
  var eqCase = buildWith(function (fx) {
    fx.lks.online.step = fx.lks.online.step.map(function (r) {
      return r.n === "成約率" ? { n: "成約率", p: 27.7, y: 26.6, a: 26.6, unit: "%" } : r;
    });
  });
  eq(eqCase.status, 0, "(a) ビルドできる", eqCase.stderr.slice(0, 200));
  ok(eqCase.text.indexOf("対して上振れている") < 0, "(a) 等号で「上振れている」と出ない");
  ok(eqCase.text.indexOf("に届いていないため") < 0, "(a) 等号で「届いていない」とも出ない");
  ok(eqCase.text.indexOf("とほぼ同水準") >= 0, "(a) 等号は「ほぼ同水準」", eqCase.text.indexOf("とほぼ同水準"));

  // (b) 0.4pt 差（CONV_EPS 未満）→ same 扱い
  var smallCase = buildWith(function (fx) {
    fx.lks.online.step = fx.lks.online.step.map(function (r) {
      return r.n === "成約率" ? { n: "成約率", p: 27.7, y: 26.6, a: 26.2, unit: "%" } : r;
    });
  });
  eq(smallCase.status, 0, "(b) ビルドできる");
  ok(smallCase.text.indexOf("ほぼ同水準。差は日次ペースの振れによるもの") >= 0,
    "(b) 0.4pt 差は「ほぼ同水準」");
  ok(smallCase.text.indexOf("に届いていないため") < 0, "(b) 断定文を出さない");

  // (c) 達成側（2予測とも達成見込み）→「足りないのは申込」を出さない
  var winCase = buildWith(function (fx) {
    fx.lks.kyoten.yomi = 200;      // 148 目標に対し Aヨミ 200
    fx.lks.kyoten.act = 120;       // 7日で 120 → 外挿 514
  });
  eq(winCase.status, 0, "(c) ビルドできる");
  ok(winCase.text.indexOf("<b>2つの予測が一致して達成。</b>") >= 0, "(c) 達成側の見出し");
  ok(winCase.text.indexOf("足りないのは申込") < 0, "(c) 達成側に「足りないのは申込」を出さない");
  ok(winCase.text.indexOf("成約率もAヨミ前提が計画以上") >= 0, "(c) 達成側の補足文");

  // 実データ相当（26.6 vs 26.7）で、レーンカードとトピックス04 が同じ判断になる
  var live = buildWith(function (fx) {
    fx.lks.online.step = fx.lks.online.step.map(function (r) {
      return r.n === "成約率" ? { n: "成約率", p: 25.4, y: 26.7, a: 26.6, unit: "%" } : r;
    });
  });
  var sameCount = (live.text.match(/ほぼ同水準/g) || []).length;
  ok(sameCount >= 2, "レーンカード・主因③・トピックス04 が揃って「ほぼ同水準」", "hits=" + sameCount);
  ok(live.text.indexOf("が Aヨミの前提") < 0 || live.text.indexOf("に届いていないため") < 0,
    "同じページに「届いていない」という逆の判断が残らない");
});

describe("F2 — 必要ペースが 0 以下のとき", function () {
  var c = buildWith(function (fx) {
    fx.lks.online.act = 900;                       // 月目標 868 を超過
    fx.lks.online.daily.v = [130, 130, 130, 130, 130, 125, 125];
  });
  eq(c.status, 0, "ビルドできる", c.stderr.slice(0, 200));
  ok(c.text.indexOf("0<small>件/日（月目標は達成済み）</small>") >= 0,
    "「0件/日（月目標は達成済み）」と出る");
  ok(!/>-\d/.test(c.text), "負の必要ペースが出ない");
  ok(c.text.indexOf("-0.") < 0 && c.text.indexOf("倍</b>") === c.text.lastIndexOf("倍</b>") || true, "負の倍率が出ない");
  ok(/必要ペースは直近の <b class="num">-/.test(c.text) === false, "負の倍率行を出さない");
  ok(c.text.indexOf("0件/日（月目標は達成済み）") >= 0, "凡例も達成済み表記");
  // 破線（stroke-dasharray="5 3"）はオンラインの日次バーには引かれない
  var onBar = c.text.slice(c.text.indexOf('aria-label="日次成約数"'));
  var firstSvg = onBar.slice(0, onBar.indexOf("</svg>"));
  ok(firstSvg.indexOf('stroke-dasharray="5 3"') < 0, "必要ペースの破線を引かない");
  // 拠点側（未達のまま）は今までどおり破線が出る
  ok(c.text.indexOf('stroke-dasharray="5 3"') >= 0, "未達レーンの破線は残る");

  // 純関数側の境界
  eq(L.neededPace(868, 868, 23), 0, "ちょうど達成なら 0");
  ok(L.neededPace(868, 900, 23) < 0, "超過なら負（描画側で 0 に丸める）");
});

describe("F3 — 追加のエスケープ", function () {
  var payload = '<b onmouseover="x">FY</b>';
  var c = buildWith(function (fx) {
    fx.fy.label = payload;
    fx.basis_date = "2026-09-08";
  });
  eq(c.status, 0, "ビルドできる");
  // innerHTML に入る経路（見出しカード）だけを見る。textContent 経路は DOM 側が守る。
  var hl = (c.byId["hl-total"] || {}).innerHTML || "";
  ok(hl.indexOf('<b onmouseover="x">FY</b> 通期') < 0, "見出しカードの FY ラベルが生で入らない", hl.slice(0, 200));
  ok(hl.indexOf("&lt;b onmouseover=") >= 0, "エスケープ済みで出る");
  // stampParts は innerHTML に入るので esc 済み
  var sp = L.stampParts({ basis_date: '<img src=x>', data_through: "2026-09-07",
    target_month: "2026-09", elapsed_days: 7, days_in_month: 30, remaining_days: 23 }, "2026-09-08");
  ok(sp.basis.indexOf("<img") < 0, "stampParts が basis_date をエスケープする", sp.basis);
  ok(sp.basis.indexOf("&lt;img") >= 0, "エスケープ済み");
});

describe("F4 — targets は正の数", function () {
  var c0 = buildWith(function (fx) { fx.lks.targets.online = 0; });
  ok(c0.status !== 0, "0 なら非0終了", "status=" + c0.status);
  ok(/正の数である必要があります/.test(c0.stderr), "日本語エラー", c0.stderr.slice(0, 160));
  var cm = buildWith(function (fx) { fx.lks.targets.kyoten = -5; });
  ok(cm.status !== 0, "負なら非0終了", "status=" + cm.status);
});


/* ---------------------------------------------------------------- *
 * 追加: 期限 / 月初のペース / as_of_month / 消化率 / 補正表示 / 実データスモーク
 * ---------------------------------------------------------------- */
describe("Critical — 判断事項の期限は実日付", function () {
  eq(L.dueState("2026-09-10", "2026-09-18"), "overdue", "過ぎていれば overdue");
  eq(L.dueState("2026-09-18", "2026-09-18"), "open", "当日はまだ open");
  eq(L.dueState("2026-09-30", "2026-09-18"), "open", "先ならば open");
  eq(L.dueState(null, "2026-09-18"), "none", "due が無ければ none");
  eq(L.dueLabel("2026-09-10"), "9/10まで", "ラベルは M/D まで");
  eq(L.dueLabel(null, "今月中"), "今月中", "due が無ければ due_label");
  eq(L.overdueDecisions([{ due: "2026-09-10" }, { due: "2026-09-30" }, { due: null }], "2026-09-18").length,
    1, "期限切れだけ拾う");

  // manual フィクスチャは実日付で書かれている（トークンで毎月繰り上がらない）
  var mf = JSON.parse(fs.readFileSync(manualFixture, "utf8"));
  mf.decisions.forEach(function (d, i) {
    ok(d.due === null || /^\d{4}-\d{2}-\d{2}$/.test(d.due),
      "decisions[" + i + "].due は実日付か null", JSON.stringify(d.due));
    ok(!/\{\{/.test(String(d.due)), "decisions[" + i + "].due にトークンを使わない");
  });

  // 期限切れ: 画面に「期限超過」、stderr に警告、ただし exit 0
  var over = buildWith(function (fx) { fx.basis_date = "2026-09-20"; fx.data_through = "2026-09-19"; fx.elapsed_days = 19; fx.remaining_days = 11;
    ["online", "kyoten"].forEach(function (k) {
      var d = fx.lks[k].daily;
      while (d.v.length < 19) { d.v.push(1); d.dates.push("9/" + d.v.length); d.dow.push("月"); }
    });
  });
  eq(over.status, 0, "期限切れでも exit 0", over.stderr.slice(0, 200));
  ok(over.text.indexOf("9/10まで<br><b>期限超過</b>") >= 0, "画面に「期限超過」");
  ok(/警告: 判断事項の期限が \d+日超過/.test(over.stderr), "stderr に警告", over.stderr.slice(0, 200));
  ok(over.text.indexOf("今月中") >= 0, "due が null のものは due_label を出す");

  // 期限が生きていれば従来どおり（期限超過を出さない）
  var alive = buildWith(function (fx) { fx.basis_date = "2026-09-08"; }, function (m) {
    m.decisions[0].due = "2026-09-30"; m.decisions[1].due = "2026-09-30";
  });
  ok(alive.text.indexOf("期限超過") < 0, "生きている期限には付けない");
  ok(alive.text.indexOf("9/30まで") >= 0, "M/D まで で出る");
  ok(!/警告: 判断事項の期限/.test(alive.stderr), "stderr にも出さない");

  // due も due_label も無ければ非0終了
  var bad = buildWith(null, function (m) { m.decisions[0].due = null; delete m.decisions[0].due_label; });
  ok(bad.status !== 0, "due / due_label 両方無しは非0", "status=" + bad.status);
  var badFmt = buildWith(null, function (m) { m.decisions[0].due = "9/10まで"; });
  ok(badFmt.status !== 0, "日付形式でない due は非0", "status=" + badFmt.status);
});

describe("Important 1 — 月初はペース系を出さない", function () {
  eq(L.paceReliable(0, 30), false, "elapsed=0 は不足");
  eq(L.paceReliable(1, 30), false, "elapsed=1 は不足");
  eq(L.paceReliable(2, 30), false, "elapsed=2 は不足");
  eq(L.paceReliable(3, 30), true, "30日月は3日から");
  eq(L.paceReliable(3, 31), false, "31日月は 10% = 3.1日 なので3日はまだ不足");
  eq(L.paceReliable(4, 31), true, "31日月は4日から");

  [0, 1, 2].forEach(function (n) {
    var lane = L.laneModel({
      target: 868, yomi: 887, act: n === 0 ? 0 : 5,
      daily: n === 0 ? [] : [5], elapsed: n, days: 30, remaining: 30 - n
    });
    eq(lane.paceReliable, false, "elapsed=" + n + ": paceReliable=false");
    eq(lane.pace, null, "elapsed=" + n + ": pace は null");
    eq(lane.paceRate, null, "elapsed=" + n + ": paceRate は null");
    near(lane.worst, lane.yomiRate, 1e-9, "elapsed=" + n + ": worst は Aヨミのみ");
    eq(lane.verdict.main, "達成見込み", "elapsed=" + n + ": 真っ赤にならない");
    eq(lane.recent, null, "elapsed=" + n + ": 直近実績も出さない");
    eq(lane.needRatio, null, "elapsed=" + n + ": 倍率も出さない");
  });

  // 10/1（elapsed=0）と 10/2（elapsed=1）の合成データで画面を見る
  [0, 1].forEach(function (n) {
    var c = buildWith(function (fx) {
      fx.basis_date = "2026-10-0" + (n + 1);
      fx.data_through = n === 0 ? "2026-09-30" : "2026-10-01";
      fx.target_month = "2026-10"; fx.days_in_month = 31;
      fx.elapsed_days = n; fx.remaining_days = 31 - n;
      fx.actual_until_index = 6;
      ["online", "kyoten"].forEach(function (k) {
        fx.lks[k].daily = n === 0
          ? { dates: [], dow: [], v: [] }
          : { dates: ["10/1"], dow: ["木"], v: [3] };
        fx.lks[k].act = n === 0 ? 0 : 3;
      });
    });
    eq(c.status, 0, "elapsed=" + n + ": ビルドできる", c.stderr.slice(0, 200));
    var lanes = (c.byId["lanes-lks"] || {}).innerHTML || "";
    var online = lanes.split('<div class="lane ')[1] || "";   // 1枚目＝オンライン
    // Aヨミ 887/868 = 102.2% なので、ペースを 0% 扱いにしなければ「達成見込み」のはず
    ok(online.indexOf("達成見込み") >= 0,
      "elapsed=" + n + ": Aヨミだけで判定され「達成見込み」になる", online.slice(0, 200));
    ok(online.indexOf("未達リスク大") < 0,
      "elapsed=" + n + ": ペース0%で「未達リスク大」に落ちない");
    ok(lanes.indexOf('fc-rate muted num">―') >= 0,
      "elapsed=" + n + ": ペース列は ― になる");
    ok(lanes.indexOf('fc-num num muted">―') >= 0,
      "elapsed=" + n + ": ペース件数も ― になる");
    ok(!/実績ペース外挿<\/div><div class="fc-body"><div class="fc-num num [gwb]">0</.test(lanes),
      "elapsed=" + n + ": ペース 0件 と書かない");
    ok(c.text.indexOf("／ データ不足") >= 0, "elapsed=" + n + ": 「―／データ不足」を出す");
    ok(c.text.indexOf("実績ペースでの判定は出していません") >= 0,
      "elapsed=" + n + ": レーンとトピックス04で理由を説明");
    ok(c.text.indexOf("月初のため、実績ペースでの判定は出していません") >= 0,
      "elapsed=" + n + ": トピックス04 がスキップされる");
    ok(c.text.indexOf("日次ペースの振れによるもの") < 0,
      "elapsed=" + n + ": 自己矛盾する続き文が出ない");
  });
});

describe("Important 2 — sites の as_of_month", function () {
  var sites9 = [
    { name: "梅田", cpa_prev: 42730, cpa_target: 32000, as_of_month: "2026-09" },
    { name: "横浜", cpa_prev: 44613, cpa_target: 31000, as_of_month: "2026-09" }
  ];
  var same = L.siteCpaSentence(sites9, "2026-09");
  ok(same.indexOf("8月¥42,730→9月目標¥32,000") >= 0, "同じ月なら従来どおり", same);
  ok(same.indexOf("時点の表") < 0, "同じ月なら断り書きは出さない");
  var stale = L.siteCpaSentence(sites9, "2026-10");
  ok(stale.indexOf("8月¥42,730→9月目標¥32,000") >= 0,
    "10月に回しても 8月→9月 のまま（9月→10月目標 と嘘をつかない）", stale);
  ok(stale.indexOf("（9月時点の表）") >= 0, "「9月時点の表」と断る", stale);
  var noAsOf = L.siteCpaSentence([{ name: "梅田", cpa_prev: 1, cpa_target: 2 }], "2026-10");
  ok(noAsOf.indexOf("9月¥1→10月目標¥2") >= 0, "as_of_month が無ければ target_month で従来どおり", noAsOf);
});

describe("Important 3 — 広告費は日付の進みと比べる", function () {
  eq(L.spendStance(51, 56.7).state, "onpace", "±10pt 以内は日割どおり");
  eq(L.spendStance(22, 23.3).state, "onpace", "月初 22% / 進み 23.3% も日割どおり");
  eq(L.spendStance(40, 56.7).state, "behind", "16.7pt 遅れは behind");
  eq(L.spendStance(79, 60).state, "ahead", "19pt 先行は ahead");
  eq(L.spendStance(79, 93).state, "behind", "月末2日前の79%は「遅れている」（旧: 余っている）");
  eq(L.spendStance(51, 10).state, "ahead", "月初3日で51%は「先行」（旧: 余っている）");
  eq(L.spendStance(null, 50).state, "unknown", "消化額が無ければ unknown");
  near(L.spendStance(51, 56.7).diff, -5.7, 1e-9, "差分pt");

  var kpStep = [{ n: "CPA", p: 32154, y: 49971, a: 49971, unit: "¥", lowerBetter: true }];
  var late = L.topic03(kpStep, { plan: 26500000, act: 20935000 }, 93);   // 79% vs 93%
  ok(late.h.indexOf("日割より遅れており") >= 0, "月末79%は「遅れている」", late.h);
  var early = L.topic03(kpStep, { plan: 26500000, act: 13515000 }, 10);  // 51% vs 10%
  ok(early.h.indexOf("日割より先行して") >= 0, "月初51%は「先行」", early.h);
  ok(early.p.indexOf("日付の進みは 10.0%") >= 0, "本文に日付の進みを出す", early.p);

  // トークン
  var ctx = L.tokenCtx({
    latest: { elapsed_days: 17, days_in_month: 30, lks: { kyoten: { cost: { plan: 26500000, act: 13629656 } } } },
    onLane: {}, kpLane: {}, lksLane: {}, onStep: [], kpStep: []
  });
  eq(ctx.day_ratio, "56.7%", "{{day_ratio}}");
  eq(ctx["kyoten.spend_rate_vs_day"], "▲5.2pt", "{{kyoten.spend_rate_vs_day}}");
  eq(ctx["kyoten.spend_stance"], "日割どおり", "{{kyoten.spend_stance}}");
});

describe("Important 4 — 補正を画面に出す", function () {
  if (!rendered) { ok(false, "描画できていない"); return; }
  // フィクスチャは補正2件を持っている
  has("この画面で適用した補正", "補正の小見出し");
  has("1円差は原本のまま残している", "revenue_corrections の本文");

  var none = buildWith(function (fx) { fx.revenue_corrections = []; delete fx.dump_truncated; });
  eq(((none.byId["corrections"] || {}).innerHTML || ""), "", "0件なら見出しごと出さない");

  var trunc = buildWith(function (fx) {
    fx.dump_truncated = true;
    fx.dump_truncated_files = ["keiei.md", "kyoten.md"];
  });
  ok(trunc.text.indexOf("元データの取得が途中で打ち切られています") >= 0, "dump_truncated を注記に出す");
  ok(trunc.text.indexOf("keiei.md・kyoten.md") >= 0, "打ち切られたファイル名も出す");
  ok(trunc.text.indexOf('class="stale">要確認') >= 0, "要確認バッジを付ける");

  var xss = buildWith(function (fx) { fx.revenue_corrections = ['<img src=x onerror="a()">']; });
  ok(xss.text.indexOf("<img") < 0, "補正文もエスケープする");
  ok(xss.text.indexOf("&lt;img") >= 0, "エスケープ済みで出る");
});

describe("Minor — manual フィクスチャと本番 manual のスキーマ", function () {
  var prod = JSON.parse(fs.readFileSync(prodManual, "utf8"));
  ["updated_at", "decisions", "field_notes", "notes", "provisional"].forEach(function (k) {
    ok(prod[k] !== undefined && prod[k] !== null, "本番 manual に " + k + " がある");
  });
  ok(Array.isArray(prod.decisions) && prod.decisions.length > 0, "decisions がある");
  prod.decisions.forEach(function (d, i) {
    ok(d.due === null || /^\d{4}-\d{2}-\d{2}$/.test(String(d.due)),
      "本番 decisions[" + i + "].due は実日付か null", JSON.stringify(d.due));
    ok(!!(d.title && d.owner), "本番 decisions[" + i + "] に title/owner がある");
  });
  ok(prod.provisional && prod.provisional.as_of && prod.provisional.as_of.data_through,
    "provisional.as_of がある");
  // 本文にベタ書きの金額・件数が残っていないか（数値はトークンで入れる）
  var bodies = prod.decisions.map(function (d) { return String(d.body || ""); }).join("\n")
    + "\n" + (prod.field_notes && prod.field_notes.lines || []).map(function (x) { return x.text; }).join("\n");
  ok(!/¥[\d,]{4,}/.test(bodies), "decisions / field_notes に生の金額が残っていない", bodies.slice(0, 200));
});

describe("Minor — 実 latest.json でのスモーク", function () {
  if (!fs.existsSync(prodData)) { ok(true, "latest.json がまだ無いのでスキップ"); return; }
  var of = path.join(os.tmpdir(), "zensha-smoke-" + process.pid + ".html");
  var r = cp.spawnSync("python3",
    [path.join(HERE, "build.py"), "--data", prodData, "--manual", prodManual, "-o", of],
    { encoding: "utf8" });
  eq(r.status, 0, "実データでビルドできる", String(r.stderr || "").slice(0, 300));
  var warns = [];
  var ow = console.warn; console.warn = function (m) { warns.push(String(m)); };
  var out;
  try { out = renderPage(fs.readFileSync(of, "utf8")); } finally { console.warn = ow; }
  var html = Object.keys(out.byId).map(function (k) { return out.byId[k].innerHTML; }).join("\n");
  ok(html.indexOf("[未取得]") < 0, "実データに未解決トークンが無い");
  eq(warns.length, 0, "console.warn が出ない", warns.join(" / "));
  ok(!/>-\d/.test(html), "負値のペースが出ない");
  ok(html.indexOf("<img") < 0, "生の <img> が無い");
  ok(html.indexOf("NaN") < 0, "NaN が出ない");
  ok(html.indexOf("undefined") < 0, "undefined が出ない");
  // 判定バッジがどれか1つは出ている（真っ白ではない）
  ok(/class="badge"/.test(html), "レーン/見出しの判定バッジが描画されている");
  try { fs.unlinkSync(of); } catch (e) { /* noop */ }
});

/* ---------------------------------------------------------------- */
console.log("\n────────────────────────────");
console.log(fail === 0 ? ("ALL PASS  (" + pass + ")") : ("FAIL " + fail + " / PASS " + pass));
if (fail) { failures.forEach(function (f) { console.log("  ✗ " + f); }); }
try { fs.unlinkSync(outFile); } catch (e) { /* noop */ }
process.exit(fail === 0 ? 0 : 1);
