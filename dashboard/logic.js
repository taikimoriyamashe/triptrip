/*!
 * logic.js — 財務売上着地モニター 計算ロジック
 *
 * 純関数のみ / DOM 非依存。スナップショット経路(build.py が埋め込んだ latest.json)と
 * ライブ経路(BigQuery MCP の実行結果)が同じ関数を通る。
 *
 * 契約: data-contract v1.2（リポジトリ外の設計文書）。列名・計算式は契約どおり。変更しないこと。
 *
 * export:
 *   parseBqResult(bqJson[, rows])  -> 型付き rows[]
 *   buildModel(data, opts)         -> 表示用モデル
 *   normalizeTargets(base, override)   -> 目標の正規化（出所つき）
 *   targetDiff(landingCentral, target) -> 目標差分・達成率
 *   convProgress({plan, actual, remainingDays, pace}) -> 成約件数の進捗
 *   （補助）CHANNELS, QUERY_KEYS, defaultPaces, lastDayOfMonth, prevMonth, fmt*
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (root) root.FALogic = api;
})(typeof self !== "undefined" ? self : typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* ------------------------------------------------------------------ *
   * 定数
   * ------------------------------------------------------------------ */

  var QUERY_KEYS = [
    "q1_official_monthly",
    "q2_lks_pending",
    "q3_mny_pd_pending",
    "q4_lks_channel_booked",
    "q5_conv_profile",
    "q6_conv_actuals",
    "q7_conv_plan",
    "q8_yomi"
  ];

  /** targets.json が無い / 欠けているときの既定（契約 v1.2）。 */
  var DEFAULT_TARGETS = {
    fa_targets: { lks: null, mny: null, pd: null, total: null },
    kyoten_conv_target: null,
    yomi: { comparable: false, note: "社内売上ヨミ(オンライン)は財務会計と定義が異なる可能性があるため参考値" },
    note: ""
  };

  var SERVICE_KEYS = ["lks", "mny", "pd", "total"];

  var CH_ONLINE = "オンライン";
  var CH_KYOTEN = "拠点";
  var CH_UNKNOWN = "分類不能";
  var CH_NORECORD = "成約記録なし";

  /** 表示順。q4 に現れない channel も 0 行として並べる。 */
  var CHANNELS = [CH_ONLINE, CH_KYOTEN, CH_UNKNOWN, CH_NORECORD];

  /** 成約プロファイルを持ちうる channel（成約記録なしは定義上プロファイル無し）。 */
  var CONV_CHANNELS = [CH_ONLINE, CH_KYOTEN, CH_UNKNOWN];

  /** q5 の分母が 0 のときの拠点スケール係数（スレッド実務値）。 */
  var K_FALLBACK = 0.94;

  /** ④滞納/処理ラグの既定織り込み率（7月実績キャリブレーション由来）。 */
  var DEFAULT_LAG_WEIGHT = 0.5;

  /**
   * 月初判定のしきい値（basis_dom がこれ以下なら「月初」）。
   * 月初は ②既成約未計上・③今後の成約 がほぼ 0 になり、当月トークンも月内に順次
   * 生成されるため、着地見込みが構造的に過少へ出る。R1 データレビューの実測に基づく。
   */
  var MONTH_START_DOM = 5;

  /* ------------------------------------------------------------------ *
   * 小さなユーティリティ（すべて純関数）
   * ------------------------------------------------------------------ */

  /** 数値化。null / undefined / 非有限 は null を返す（0 は 0 のまま）。 */
  function num(v) {
    if (v === null || v === undefined || v === "") return null;
    var n = typeof v === "number" ? v : Number(v);
    return isFinite(n) ? n : null;
  }

  /** 数値化。取れなければ既定値（合計用）。 */
  function n0(v, d) {
    var n = num(v);
    return n === null ? (d === undefined ? 0 : d) : n;
  }

  function isArr(v) { return Object.prototype.toString.call(v) === "[object Array]"; }

  function rowsOf(data, key) {
    if (!data || !data.queries || !data.queries[key]) return [];
    var r = data.queries[key].rows;
    return isArr(r) ? r : [];
  }

  /** 'YYYY-MM' の末日。 */
  function lastDayOfMonth(ym) {
    var m = /^(\d{4})-(\d{2})/.exec(String(ym || ""));
    if (!m) return 31;
    return new Date(Date.UTC(+m[1], +m[2], 0)).getUTCDate();
  }

  /** 'YYYY-MM' の前月。 */
  function prevMonth(ym) {
    var m = /^(\d{4})-(\d{2})/.exec(String(ym || ""));
    if (!m) return "";
    var y = +m[1], mo = +m[2] - 1;
    if (mo < 1) { mo = 12; y -= 1; }
    return y + "-" + (mo < 10 ? "0" + mo : "" + mo);
  }

  /** 'YYYY-MM-DD' の日。取れなければ 1。 */
  function domOf(dateStr) {
    var m = /^\d{4}-\d{2}-(\d{2})/.exec(String(dateStr || ""));
    return m ? +m[1] : 1;
  }

  /** 'YYYY-MM-DD' → 'YYYY-MM'。 */
  function monthOf(dateStr) {
    var m = /^(\d{4}-\d{2})/.exec(String(dateStr || ""));
    return m ? m[1] : "";
  }

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  /* ------------------------------------------------------------------ *
   * parseBqResult — BigQuery REST 形式 → 型付き rows
   * ------------------------------------------------------------------ */

  var INT_TYPES = { INTEGER: 1, INT64: 1, INT: 1 };
  var FLOAT_TYPES = { FLOAT: 1, FLOAT64: 1, NUMERIC: 1, BIGNUMERIC: 1, DECIMAL: 1, DOUBLE: 1 };
  var BOOL_TYPES = { BOOL: 1, BOOLEAN: 1 };

  function coerce(v, type) {
    if (v === null || v === undefined) return null;
    var t = String(type || "STRING").toUpperCase();
    if (INT_TYPES[t] || FLOAT_TYPES[t]) {
      if (v === "") return null;
      var n = Number(v);
      return isFinite(n) ? n : null;
    }
    if (BOOL_TYPES[t]) {
      if (typeof v === "boolean") return v;
      return String(v).toLowerCase() === "true";
    }
    return typeof v === "string" ? v : String(v);
  }

  /**
   * BigQuery の結果を型付きオブジェクト行に変換する。
   *
   * 受け付ける形:
   *   parseBqResult(payloadString)                       // JSON 文字列
   *   parseBqResult({schema:{fields:[..]}, rows:[{f:[..]}]})
   *   parseBqResult({rows:[{col:val,...}]})              // 既に平坦な行
   *   parseBqResult([{name,type},...], [{f:[...]}, ...]) // schema.fields, rows を別引数で
   *   parseBqResult({payload: <上のいずれか>})            // MCP CallToolResult そのまま
   *
   * NULL は null、INT/FLOAT は number、その他は string。
   */
  function parseBqResult(bqJson, maybeRows) {
    var src = bqJson;

    if (typeof src === "string") {
      try { src = JSON.parse(src); } catch (e) { return []; }
    }
    if (!src) return [];

    // schema.fields を第1引数、rows を第2引数で渡された形
    if (isArr(src) && isArr(maybeRows)) {
      return mapRows({ fields: src }, maybeRows);
    }
    if (isArr(src)) {
      // 既に平坦な行配列
      return src.slice();
    }

    // MCP CallToolResult / ネストした payload を剥がす
    var guard = 0;
    while (src && typeof src === "object" && !src.rows && !src.schema && guard < 5) {
      var next = src.payload !== undefined ? src.payload
        : src.structuredContent !== undefined ? src.structuredContent
          : src.result !== undefined ? src.result : null;
      if (next === null || next === undefined) break;
      if (typeof next === "string") {
        try { next = JSON.parse(next); } catch (e) { return []; }
      }
      src = next;
      guard++;
    }
    if (!src || typeof src !== "object") return [];
    if (isArr(src)) return src.slice();

    var rows = isArr(src.rows) ? src.rows : (isArr(maybeRows) ? maybeRows : []);
    var schema = src.schema || (isArr(bqJson) ? { fields: bqJson } : null);
    return mapRows(schema, rows);
  }

  function mapRows(schema, rows) {
    var fields = schema && isArr(schema.fields) ? schema.fields : null;
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (!r || typeof r !== "object") continue;
      if (!isArr(r.f)) {
        // 既に平坦なオブジェクト行。schema があれば型だけ整える。
        if (!fields) { out.push(r); continue; }
        var flat = {};
        for (var k = 0; k < fields.length; k++) {
          var fn = fields[k].name;
          flat[fn] = coerce(r[fn], fields[k].type);
        }
        // schema に無いキーもそのまま残す
        for (var key in r) if (Object.prototype.hasOwnProperty.call(r, key) && !(key in flat)) flat[key] = r[key];
        out.push(flat);
        continue;
      }
      var o = {};
      for (var j = 0; j < r.f.length; j++) {
        var f = fields && fields[j] ? fields[j] : { name: "f" + j, type: "STRING" };
        var cell = r.f[j];
        var v = cell && typeof cell === "object" ? cell.v : cell;
        o[f.name] = coerce(v, f.type);
      }
      out.push(o);
    }
    return out;
  }

  /* ------------------------------------------------------------------ *
   * 計算コア
   * ------------------------------------------------------------------ */

  /** rate(row) = fa_per_day_cur ?? fa_per_day_prev ?? 0 */
  function rateOf(row) {
    var cur = num(row && row.fa_per_day_cur);
    if (cur !== null) return { rate: cur, source: "cur" };
    var prev = num(row && row.fa_per_day_prev);
    if (prev !== null) return { rate: prev, source: "prev" };
    return { rate: 0, source: "none" };
  }

  /**
   * q5 から成約日別プロファイルを組む。
   * prof(オンライン,dom) = q5 オンラインの fa_per_conv（欠損 dom は 0）
   * prof(拠点,dom)       = prof(オンライン,dom) × k
   * prof(分類不能,dom)   = prof(オンライン,dom)
   * k = 拠点の月計 fa_per_conv 加重平均 ÷ オンライン同（分母0なら K_FALLBACK）
   */
  function buildProfile(q5, lastDom) {
    var online = {};
    var agg = {};   // channel -> {num, den}
    for (var i = 0; i < q5.length; i++) {
      var r = q5[i];
      var ch = r && r.channel;
      var dom = num(r && r.dom);
      var fpc = n0(r && r.fa_per_conv, 0);
      var nc = n0(r && r.n_conv, 0);
      if (!agg[ch]) agg[ch] = { num: 0, den: 0 };
      agg[ch].num += nc * fpc;
      agg[ch].den += nc;
      if (ch === CH_ONLINE && dom !== null) online[dom] = fpc;
    }

    var onW = agg[CH_ONLINE] && agg[CH_ONLINE].den > 0 ? agg[CH_ONLINE].num / agg[CH_ONLINE].den : 0;
    var kyW = agg[CH_KYOTEN] && agg[CH_KYOTEN].den > 0 ? agg[CH_KYOTEN].num / agg[CH_KYOTEN].den : null;

    var k, kSource;
    if (onW > 0 && kyW !== null) { k = kyW / onW; kSource = "q5"; }
    else { k = K_FALLBACK; kSource = "fallback"; }

    function prof(ch, dom) {
      var base = online[dom];
      if (base === undefined || base === null || !isFinite(base)) base = 0;
      if (ch === CH_ONLINE) return base;
      if (ch === CH_KYOTEN) return base * k;
      if (ch === CH_UNKNOWN) return base;
      return 0; // 成約記録なし 等
    }

    // dom 1..lastDom のオンライン系列（チャート/表用）
    var onlineSeries = [];
    for (var d = 1; d <= lastDom; d++) onlineSeries.push({ dom: d, fa_per_conv: prof(CH_ONLINE, d) });

    return {
      prof: prof,
      k: k,
      kSource: kSource,
      onlineWeightedAvg: onW,
      kyotenWeightedAvg: kyW,
      onlineSeries: onlineSeries,
      hasOnline: onW > 0
    };
  }

  /** 当月 n_valid 合計 ÷ max(1, basis_dom−1) を小数1桁（channel ごと）。 */
  function defaultPaces(q6, basisDom) {
    var by = {};
    for (var i = 0; i < q6.length; i++) {
      var r = q6[i];
      var ch = r && r.channel;
      if (!by[ch]) by[ch] = 0;
      by[ch] += n0(r && r.n_valid, 0);
    }
    var den = Math.max(1, basisDom - 1);
    function pace(ch) { return Math.round(((by[ch] || 0) / den) * 10) / 10; }
    return {
      paceOn: pace(CH_ONLINE),
      paceKyoten: pace(CH_KYOTEN),
      paceUnknown: 0,               // 契約: 分類不能のデフォルトは 0
      validTotals: {
        "オンライン": by[CH_ONLINE] || 0,
        "拠点": by[CH_KYOTEN] || 0,
        "分類不能": by[CH_UNKNOWN] || 0
      },
      denom: den
    };
  }

  function scen(base, extra, w) {
    return { low: base, central: base + w * extra, high: base + extra };
  }

  /* ------------------------------------------------------------------ *
   * 目標（targets.json / UI 上書き） — 契約 v1.2
   * ------------------------------------------------------------------ */

  /**
   * repo の targets.json と、閲覧者ローカルの上書きを畳んで正規化する。
   * 値ごとに出所（repo / local / derived）を持たせ、UI が「どこから来た数字か」
   * を明示できるようにする。純関数。
   *
   * @param {object} base     data.targets（build.py が注入した targets.json）
   * @param {object} override UI/localStorage 由来の部分上書き（同じ形）
   */
  function normalizeTargets(base, override) {
    base = base && typeof base === "object" ? base : {};
    override = override && typeof override === "object" ? override : {};

    var baseFa = (base.fa_targets && typeof base.fa_targets === "object") ? base.fa_targets : {};
    var ovFa = (override.fa_targets && typeof override.fa_targets === "object") ? override.fa_targets : {};

    var fa = {};
    for (var i = 0; i < SERVICE_KEYS.length; i++) {
      var k = SERVICE_KEYS[i];
      var ov = num(ovFa[k]);
      var bv = num(baseFa[k]);
      if (ov !== null && ov > 0) fa[k] = { value: ov, source: "local" };
      else if (bv !== null && bv > 0) fa[k] = { value: bv, source: "repo" };
      else fa[k] = { value: null, source: null };
    }

    // total 未設定でも 3 サービスすべてに目標があれば合計を導出（出所を derived と明示）
    if (fa.total.value === null) {
      var sum = 0, all = true;
      ["lks", "mny", "pd"].forEach(function (k2) {
        if (fa[k2].value === null) all = false; else sum += fa[k2].value;
      });
      if (all && sum > 0) fa.total = { value: sum, source: "derived" };
    }

    var ovK = num(override.kyoten_conv_target);
    var baseK = num(base.kyoten_conv_target);
    var kyoten;
    if (ovK !== null && ovK >= 0) kyoten = { value: ovK, source: "local" };
    else if (baseK !== null && baseK >= 0) kyoten = { value: baseK, source: "repo" };
    else kyoten = { value: null, source: null };

    var baseYomi = (base.yomi && typeof base.yomi === "object") ? base.yomi : {};
    var ovYomi = (override.yomi && typeof override.yomi === "object") ? override.yomi : {};
    var comparable = ovYomi.comparable !== undefined ? !!ovYomi.comparable
      : baseYomi.comparable !== undefined ? !!baseYomi.comparable
        : DEFAULT_TARGETS.yomi.comparable;

    var hasAnyFa = false;
    for (var j = 0; j < SERVICE_KEYS.length; j++) if (fa[SERVICE_KEYS[j]].value !== null) hasAnyFa = true;

    return {
      fa: fa,
      kyotenConvTarget: kyoten,
      yomi: {
        comparable: comparable,
        note: (ovYomi.note || baseYomi.note || DEFAULT_TARGETS.yomi.note)
      },
      note: base.note || "",
      hasAnyFa: hasAnyFa,
      hasLocalOverride: (function () {
        for (var x = 0; x < SERVICE_KEYS.length; x++) if (fa[SERVICE_KEYS[x]].source === "local") return true;
        return kyoten.source === "local";
      })()
    };
  }

  /**
   * 着地見込み(central) と目標の差分。
   * 目標が未設定 / 0 以下なら null（呼び出し側は前月比にフォールバックする）。
   *
   * @returns {null|{target,diff,rate,met,shortfall}}
   */
  function targetDiff(landingCentral, target) {
    var t = num(target);
    var l = num(landingCentral);
    if (t === null || !(t > 0) || l === null) return null;
    var diff = l - t;
    return {
      target: t,
      diff: diff,
      rate: l / t,
      met: diff >= 0,
      shortfall: Math.max(0, -diff)
    };
  }

  /**
   * 成約件数の進捗（計画 vs 実績 vs 現ペース着地）。
   *
   * ゼロ除算・欠損の扱い:
   *  - plan が無い → remaining / neededPace / rate / projectedDiff はすべて null
   *  - 実績が計画以上 → remaining = 0、neededPace = 0（負にしない）
   *  - 残日数 0 でまだ残がある → neededPace は算出不能(null) かつ unreachable = true
   *
   * @param {{plan:?number, actual:number, remainingDays:number, pace:?number,
   *          label:?string, src:?string, asOf:?string}} o
   */
  function convProgress(o) {
    o = o || {};
    var plan = num(o.plan);
    if (plan !== null && plan < 0) plan = 0;
    var actual = Math.max(0, n0(o.actual, 0));
    var days = Math.max(0, n0(o.remainingDays, 0));
    var pace = num(o.pace);
    if (pace === null || pace < 0) pace = 0;

    var remaining = plan === null ? null : Math.max(0, plan - actual);
    var neededPace = null;
    var unreachable = false;
    if (remaining !== null) {
      if (remaining === 0) neededPace = 0;
      else if (days <= 0) { neededPace = null; unreachable = true; }
      else neededPace = remaining / days;
    }

    var projected = actual + pace * days;
    var behind = unreachable || (neededPace !== null && neededPace > pace);

    return {
      label: o.label || "",
      plan: plan,
      hasPlan: plan !== null,
      actual: actual,
      remaining: remaining,
      remainingDays: days,
      neededPace: neededPace,
      unreachable: unreachable,
      pace: pace,
      projected: projected,
      projectedDiff: plan === null ? null : projected - plan,
      rate: (plan !== null && plan > 0) ? actual / plan : null,
      projectedRate: (plan !== null && plan > 0) ? projected / plan : null,
      behind: behind,
      src: o.src || null,
      asOf: o.asOf || null
    };
  }

  /* ------------------------------------------------------------------ *
   * buildModel
   * ------------------------------------------------------------------ */

  /**
   * @param {object} data   latest.json 形状（generated_at / basis_date / target_month / queries / meta）
   * @param {object} [opts] {paceOn, paceKyoten, paceUnknown, lagWeight}
   */
  function buildModel(data, opts) {
    data = data || {};
    opts = opts || {};

    var q1 = rowsOf(data, "q1_official_monthly");
    var q2 = rowsOf(data, "q2_lks_pending");
    var q3 = rowsOf(data, "q3_mny_pd_pending");
    var q4 = rowsOf(data, "q4_lks_channel_booked");
    var q5 = rowsOf(data, "q5_conv_profile");
    var q6 = rowsOf(data, "q6_conv_actuals");
    var q7 = rowsOf(data, "q7_conv_plan");
    var q8 = rowsOf(data, "q8_yomi");

    var basisDate = data.basis_date || "";
    var targetMonth = data.target_month || monthOf(basisDate) || "";
    var basisDom = domOf(basisDate);
    var lastDom = lastDayOfMonth(targetMonth);
    if (basisDom > lastDom) basisDom = lastDom;
    var remainingDays = Math.max(0, lastDom - basisDom + 1);
    var pm = prevMonth(targetMonth);

    // --- q1 履歴 ---
    var hist = q1.slice().sort(function (a, b) {
      return String(a.year_month) < String(b.year_month) ? -1 : String(a.year_month) > String(b.year_month) ? 1 : 0;
    }).map(function (r) {
      var lks = n0(r.lks, 0), mny = n0(r.mny, 0), pd = n0(r.pd, 0);
      return {
        ym: String(r.year_month || ""),
        lks: lks, mny: mny, pd: pd, total: lks + mny + pd,
        isCurrent: String(r.year_month) === targetMonth,
        isFuture: String(r.year_month) > targetMonth
      };
    });
    function official(ym) {
      for (var i = 0; i < hist.length; i++) if (hist[i].ym === ym) return hist[i];
      return { ym: ym, lks: 0, mny: 0, pd: 0, total: 0, isCurrent: false, isFuture: false, missing: true };
    }
    var cur = official(targetMonth);
    var prv = official(pm);

    // --- プロファイルとペース ---
    var profile = buildProfile(q5, lastDom);
    var paceDefaults = defaultPaces(q6, basisDom);
    var lagWeight = num(opts.lagWeight);
    if (lagWeight === null) lagWeight = DEFAULT_LAG_WEIGHT;
    lagWeight = clamp(lagWeight, 0, 1);

    var paceOn = num(opts.paceOn); if (paceOn === null) paceOn = paceDefaults.paceOn;
    var paceKy = num(opts.paceKyoten); if (paceKy === null) paceKy = paceDefaults.paceKyoten;
    var paceUn = num(opts.paceUnknown); if (paceUn === null) paceUn = paceDefaults.paceUnknown;
    paceOn = Math.max(0, paceOn); paceKy = Math.max(0, paceKy); paceUn = Math.max(0, paceUn);
    var paceByCh = {};
    paceByCh[CH_ONLINE] = paceOn; paceByCh[CH_KYOTEN] = paceKy; paceByCh[CH_UNKNOWN] = paceUn; paceByCh[CH_NORECORD] = 0;

    // 残日 basis_dom..last_dom のプロファイル合計（channel 別）
    var remainProf = {};
    for (var ci = 0; ci < CHANNELS.length; ci++) {
      var chName = CHANNELS[ci], s = 0;
      for (var d = basisDom; d <= lastDom; d++) s += profile.prof(chName, d);
      remainProf[chName] = s;
    }

    /* ---------------- LKS ---------------- */

    // ① 更新残 P1 / ④ 滞納・処理ラグ P4
    var P1 = 0, P4 = 0, nWindowTotal = 0, nLagTotal = 0, windowDaysTotal = 0, lagDaysTotal = 0;
    var planRows = [];
    var anyCurRate = false;
    for (var i2 = 0; i2 < q2.length; i2++) {
      var r2 = q2[i2];
      var ro = rateOf(r2);
      if (ro.source === "cur") anyCurRate = true;
      var wd = n0(r2.window_days, 0), ld = n0(r2.lag_days, 0);
      var p1 = wd * ro.rate, p4 = ld * ro.rate;
      P1 += p1; P4 += p4;
      nWindowTotal += n0(r2.n_window, 0); nLagTotal += n0(r2.n_lag, 0);
      windowDaysTotal += wd; lagDaysTotal += ld;
      planRows.push({
        plan_name: r2.plan_name === undefined || r2.plan_name === null ? "" : String(r2.plan_name),
        payment_type: r2.payment_type === undefined || r2.payment_type === null ? "" : String(r2.payment_type),
        rate: ro.rate, rateSource: ro.source,
        fa_per_day_cur: num(r2.fa_per_day_cur), fa_per_day_prev: num(r2.fa_per_day_prev),
        n_window: n0(r2.n_window, 0), window_days: wd, p1: p1,
        n_lag: n0(r2.n_lag, 0), lag_days: ld, p4: p4
      });
    }
    planRows.sort(function (a, b) { return b.p1 - a.p1; });

    // ② 既成約未計上 P2 = Σ_{ch, dom<basis_dom} max(0, n_valid×prof − booked_fa_valid)
    var P2 = 0;
    var p2ByCh = {}, convByCh = {}, convByDom = {};
    for (var c3 = 0; c3 < CHANNELS.length; c3++) {
      p2ByCh[CHANNELS[c3]] = 0;
      convByCh[CHANNELS[c3]] = { channel: CHANNELS[c3], nAll: 0, nValid: 0, bookedFaValid: 0, expected: 0, shortfall: 0 };
    }
    for (var i6 = 0; i6 < q6.length; i6++) {
      var r6 = q6[i6];
      var ch6 = r6 && r6.channel ? String(r6.channel) : CH_UNKNOWN;
      var dom6 = num(r6 && r6.dom);
      if (dom6 === null) continue;
      var nv = n0(r6.n_valid, 0), na = n0(r6.n_all, 0), bf = n0(r6.booked_fa_valid, 0);
      if (!convByCh[ch6]) convByCh[ch6] = { channel: ch6, nAll: 0, nValid: 0, bookedFaValid: 0, expected: 0, shortfall: 0 };
      convByCh[ch6].nAll += na; convByCh[ch6].nValid += nv; convByCh[ch6].bookedFaValid += bf;
      if (!convByDom[dom6]) convByDom[dom6] = { dom: dom6, nAll: 0, nValid: 0, bookedFaValid: 0 };
      convByDom[dom6].nAll += na; convByDom[dom6].nValid += nv; convByDom[dom6].bookedFaValid += bf;
      var expected = nv * profile.prof(ch6, dom6);
      convByCh[ch6].expected += expected;
      if (dom6 < basisDom) {
        var short = Math.max(0, expected - bf);
        P2 += short;
        convByCh[ch6].shortfall += short;
        if (p2ByCh[ch6] === undefined) p2ByCh[ch6] = 0;
        p2ByCh[ch6] += short;
      }
    }

    // ③ 今後の成約 P3 = Σ_ch pace_ch × Σ_{dom=basis_dom..last_dom} prof(ch,dom)
    var P3 = 0, p3ByCh = {};
    for (var c4 = 0; c4 < CHANNELS.length; c4++) {
      var chn = CHANNELS[c4];
      var v = (paceByCh[chn] || 0) * (remainProf[chn] || 0);
      p3ByCh[chn] = v;
      P3 += v;
    }

    var lksBooked = cur.lks;
    var lksPending = scen(P1 + P2 + P3, P4, lagWeight);
    var lksLanding = {
      low: lksBooked + lksPending.low,
      central: lksBooked + lksPending.central,
      high: lksBooked + lksPending.high
    };

    // --- チャネル別 ---
    var q4map = {}, q4sum = 0, shareDen = 0;
    for (var i4 = 0; i4 < q4.length; i4++) {
      var r4 = q4[i4];
      var ch4 = r4 && r4.channel ? String(r4.channel) : CH_UNKNOWN;
      var bfa = n0(r4.booked_fa, 0);
      if (!q4map[ch4]) q4map[ch4] = { channel: ch4, nUsers: 0, booked: 0 };
      q4map[ch4].nUsers += n0(r4.n_users, 0);
      q4map[ch4].booked += bfa;
      q4sum += bfa;
      if (ch4 !== CH_NORECORD) shareDen += bfa;
    }
    var chOrder = CHANNELS.slice();
    for (var kk in q4map) if (Object.prototype.hasOwnProperty.call(q4map, kk) && chOrder.indexOf(kk) < 0) chOrder.push(kk);

    var channels = chOrder.map(function (chName2) {
      var base = q4map[chName2] || { channel: chName2, nUsers: 0, booked: 0 };
      var share = (chName2 !== CH_NORECORD && shareDen > 0) ? base.booked / shareDen : 0;
      var inc = {
        p1: share * P1,
        p2: share * P2,
        p3: p3ByCh[chName2] || 0,
        p4: share * P4
      };
      var pend = scen(inc.p1 + inc.p2 + inc.p3, inc.p4, lagWeight);
      return {
        channel: chName2,
        nUsers: base.nUsers,
        booked: base.booked,
        share: share,
        inc: inc,
        pending: pend,
        landing: { low: base.booked + pend.low, central: base.booked + pend.central, high: base.booked + pend.high },
        conv: convByCh[chName2] || { channel: chName2, nAll: 0, nValid: 0, bookedFaValid: 0, expected: 0, shortfall: 0 },
        pace: paceByCh[chName2] === undefined ? 0 : paceByCh[chName2],
        remainProf: remainProf[chName2] || 0
      };
    });

    /* ---------------- MNY ---------------- */

    var mnyRow = null, pdRow = null;
    for (var i3 = 0; i3 < q3.length; i3++) {
      var sk = String(q3[i3] && q3[i3].service_key || "");
      if (sk === "money") mnyRow = q3[i3];
      else if (sk === "multicreator") pdRow = q3[i3];
    }
    var mnyRate = rateOf(mnyRow || {});
    var mP1 = n0(mnyRow && mnyRow.window_days, 0) * mnyRate.rate;
    var mP4 = n0(mnyRow && mnyRow.lag_days, 0) * mnyRate.rate;
    var mnyBooked = cur.mny;
    var mnyPending = scen(mP1, mP4, lagWeight);
    var mny = {
      booked: mnyBooked,
      rate: mnyRate.rate, rateSource: mnyRate.source,
      fa_per_day_cur: num(mnyRow && mnyRow.fa_per_day_cur),
      fa_per_day_prev: num(mnyRow && mnyRow.fa_per_day_prev),
      components: { p1: mP1, p4: mP4 },
      counts: {
        nWindow: n0(mnyRow && mnyRow.n_window, 0), windowDays: n0(mnyRow && mnyRow.window_days, 0),
        nLag: n0(mnyRow && mnyRow.n_lag, 0), lagDays: n0(mnyRow && mnyRow.lag_days, 0),
        nActiveCur: n0(mnyRow && mnyRow.n_active_cur, 0), nActivePrev: n0(mnyRow && mnyRow.n_active_prev, 0)
      },
      pending: mnyPending,
      landing: { low: mnyBooked + mnyPending.low, central: mnyBooked + mnyPending.central, high: mnyBooked + mnyPending.high },
      prev: prv.mny,
      hasRow: !!mnyRow
    };

    /* ---------------- プロデ (multicreator) ---------------- */

    var pdBooked = cur.pd;
    var pdActivePrev = n0(pdRow && pdRow.n_active_prev, 0);
    var pdPerUnit = pdActivePrev > 0 ? prv.pd / pdActivePrev : 0;
    var pdNWindow = n0(pdRow && pdRow.n_window, 0);
    var pdUpside = pdNWindow * pdPerUnit;
    var pd = {
      booked: pdBooked,
      landing: { low: pdBooked, central: pdBooked, high: pdBooked + pdUpside },
      pending: { low: 0, central: 0, high: pdUpside },
      upside: pdUpside,
      perUnit: pdPerUnit,
      nWindow: pdNWindow,
      counts: {
        nActiveCur: n0(pdRow && pdRow.n_active_cur, 0),
        nActivePrev: pdActivePrev,
        windowDays: n0(pdRow && pdRow.window_days, 0)
      },
      prevOfficial: prv.pd,
      prev: prv.pd,
      fa_per_day_cur: num(pdRow && pdRow.fa_per_day_cur),
      separatePipeline: true,
      hasRow: !!pdRow
    };

    /* ---------------- 合計 ---------------- */

    var total = {
      booked: lksBooked + mnyBooked + pdBooked,
      pending: {
        low: lksPending.low + mny.pending.low + pd.pending.low,
        central: lksPending.central + mny.pending.central + pd.pending.central,
        high: lksPending.high + mny.pending.high + pd.pending.high
      },
      landing: {
        low: lksLanding.low + mny.landing.low + pd.landing.low,
        central: lksLanding.central + mny.landing.central + pd.landing.central,
        high: lksLanding.high + mny.landing.high + pd.landing.high
      },
      prev: prv.total
    };

    function withDelta(o, prevVal) {
      o.prev = prevVal;
      o.delta = o.landing.central - prevVal;
      o.deltaPct = prevVal > 0 ? (o.landing.central / prevVal - 1) : null;
      return o;
    }

    var lks = withDelta({
      booked: lksBooked,
      components: { p1: P1, p2: P2, p3: P3, p4: P4 },
      componentsByCh: { p1: p2ByCh /* placeholder replaced below */ },
      pending: lksPending,
      landing: lksLanding,
      channels: channels,
      channelSum: { booked: q4sum, share_den: shareDen },
      planRows: planRows,
      totals: { nWindow: nWindowTotal, windowDays: windowDaysTotal, nLag: nLagTotal, lagDays: lagDaysTotal },
      profile: profile,
      remainProf: remainProf,
      conv: {
        byChannel: CHANNELS.map(function (c) { return convByCh[c]; }).filter(Boolean),
        byDom: Object.keys(convByDom).map(function (d2) { return convByDom[d2]; })
          .sort(function (a, b) { return a.dom - b.dom; })
      },
      anyCurRate: anyCurRate
    }, prv.lks);
    delete lks.componentsByCh;
    lks.p2ByCh = p2ByCh;
    lks.p3ByCh = p3ByCh;

    withDelta(mny, prv.mny);
    withDelta(pd, prv.pd);
    withDelta(total, prv.total);

    /* ---------------- 目標との差分（契約 v1.2） ---------------- */

    var targets = normalizeTargets(data.targets, opts.targets);
    var svcByKey = { lks: lks, mny: mny, pd: pd, total: total };
    SERVICE_KEYS.forEach(function (k) {
      var s = svcByKey[k];
      var t = targets.fa[k];
      s.target = t.value;
      s.targetSource = t.source;
      s.targetDiff = targetDiff(s.landing.central, t.value);
      // 主表示の基準線: 目標があれば目標、無ければ前月実績
      s.baseline = t.value !== null ? t.value : (s.prev > 0 ? s.prev : null);
      s.baselineKind = t.value !== null ? "target" : (s.prev > 0 ? "prev" : null);
    });

    /* ---------------- 成約件数の進捗（q7 / 拠点目標） ---------------- */

    function q7Row(ym) {
      for (var i = 0; i < q7.length; i++) if (String(q7[i].month) === ym) return q7[i];
      return null;
    }
    var planRow = q7Row(targetMonth);
    var planOnline = null, planSrc = null, planAsOf = null;
    if (planRow) {
      var pr = num(planRow.plan_regular), ps = num(planRow.plan_sutara);
      if (pr !== null || ps !== null) planOnline = (pr || 0) + (ps || 0);
      var sr = planRow.src_regular ? String(planRow.src_regular) : null;
      var ss = planRow.src_sutara ? String(planRow.src_sutara) : null;
      // 片方でも「実績」なら実績寄りの行。両方同じならそれを採用。
      planSrc = (sr && ss) ? (sr === ss ? sr : sr + "/" + ss) : (sr || ss);
      planAsOf = planRow.as_of ? String(planRow.as_of) : null;
    }

    var validTotals = paceDefaults.validTotals;
    var convProgressOnline = convProgress({
      label: CH_ONLINE,
      plan: planOnline,
      actual: validTotals[CH_ONLINE] || 0,
      remainingDays: remainingDays,
      pace: paceOn,
      src: planSrc,
      asOf: planAsOf
    });
    var convProgressKyoten = convProgress({
      label: CH_KYOTEN,
      plan: targets.kyotenConvTarget.value,
      actual: validTotals[CH_KYOTEN] || 0,
      remainingDays: remainingDays,
      pace: paceKy,
      src: targets.kyotenConvTarget.source === "local" ? "手入力(この端末)"
        : targets.kyotenConvTarget.source === "repo" ? "手入力(targets.json)" : null,
      asOf: null
    });

    /* ---------------- 社内売上ヨミ（q8） ---------------- */

    var yomiRow = null;
    for (var iy = 0; iy < q8.length; iy++) if (String(q8[iy].month) === targetMonth) yomiRow = q8[iy];
    var yomi = yomiRow ? {
      month: targetMonth,
      total: n0(yomiRow.yomi_total, 0),
      asOf: yomiRow.as_of ? String(yomiRow.as_of) : null,
      comparable: targets.yomi.comparable,
      note: targets.yomi.note,
      // comparable のときだけ「目標系列」として扱ってよい
      role: targets.yomi.comparable ? "target" : "reference",
      // オンライン売上のヨミなので、比較先は LKS 着地
      diff: targets.yomi.comparable ? targetDiff(lks.landing.central, n0(yomiRow.yomi_total, 0)) : null
    } : null;

    /* ---------------- 不変条件チェック ---------------- */

    var q6BookedSum = 0;
    for (var i7 = 0; i7 < q6.length; i7++) q6BookedSum += n0(q6[i7].booked_fa_valid, 0);

    var comps = [P1, P2, P3, P4, mP1, mP4, pdUpside];
    var allNonNeg = true;
    for (var ic = 0; ic < comps.length; ic++) if (!(comps[ic] >= 0)) allNonNeg = false;

    var q4Diff = lksBooked > 0 ? Math.abs(q4sum - lksBooked) / lksBooked : (q4sum === 0 ? 0 : 1);

    var checks = [
      {
        id: "q4_matches_q1",
        label: "Σ q4.booked_fa ≒ q1 当月 lks（±1%）",
        ok: q4Diff <= 0.01,
        detail: fmtYen(q4sum) + " / " + fmtYen(lksBooked) + "（差 " + (q4Diff * 100).toFixed(3) + "%）"
      },
      {
        id: "ordering",
        label: "low ≤ central ≤ high・各成分 ≥ 0",
        ok: allNonNeg &&
          total.landing.low <= total.landing.central + 1e-6 &&
          total.landing.central <= total.landing.high + 1e-6,
        detail: fmtMillion(total.landing.low) + " ≤ " + fmtMillion(total.landing.central) + " ≤ " + fmtMillion(total.landing.high)
      },
      {
        id: "q6_le_q1",
        label: "Σ q6.booked_fa_valid ≤ q1 当月 lks",
        ok: q6BookedSum <= lksBooked,
        detail: fmtYen(q6BookedSum) + " ≤ " + fmtYen(lksBooked)
      },
      {
        id: "mny_rate_band",
        label: "MNY の単価が 500〜900 円/日",
        ok: mny.rate >= 500 && mny.rate <= 900,
        detail: mny.rate ? mny.rate.toFixed(1) + " 円/日（" + (mny.rateSource === "prev" ? "前月単価で代替" : "当月") + "）" : "単価なし"
      },
      {
        id: "pd_separate_pipeline",
        label: "multicreator の fa_per_day_cur が NULL または 0",
        ok: pd.fa_per_day_cur === null || pd.fa_per_day_cur === 0,
        detail: pd.fa_per_day_cur === null ? "NULL（別パイプライン仕様どおり）" : String(pd.fa_per_day_cur)
      }
    ];

    var isMonthStart = basisDom <= MONTH_START_DOM;

    var meta = {
      generatedAt: data.generated_at || "",
      basisDate: basisDate,
      basisDom: basisDom,
      lastDom: lastDom,
      targetMonth: targetMonth,
      prevMonth: pm,
      remainingDays: remainingDays,
      elapsedDays: Math.max(0, basisDom - 1),
      monthProgress: lastDom > 0 ? clamp((basisDom - 1) / lastDom, 0, 1) : 0,
      isMonthStart: isMonthStart,
      monthStartDom: MONTH_START_DOM,
      bytesProcessed: (data.meta && data.meta.bytes_processed) || {},
      source: (data.meta && data.meta.source) || "",
      isFixture: !!(data.meta && data.meta.fixture)
    };

    /* ---------------- 読み手への注意（データ由来） ---------------- */

    var notices = [];
    if (isMonthStart) {
      notices.push({
        id: "month_start",
        level: "warn",
        title: "月初のため着地見込みは過少に出やすい",
        body: "基準日が " + basisDom + " 日（月初 " + MONTH_START_DOM + " 日以内）のため、" +
          "② 既成約未計上と ③ 今後の成約がほぼ 0 になり、当月トークンも月内に順次生成される。" +
          "この時点の着地見込みは構造的に低めに出る。前月実績（" + pm + "：" +
          fmtMillionNum(prv.total) + " 百万円）も目安に読むこと。"
      });
    }
    if (!profile.hasOnline) {
      notices.push({
        id: "no_profile",
        level: "warn",
        title: "成約プロファイル（q5）が空",
        body: "前月の成約日別プロファイルが取れていないため、② と ③ は 0 として扱っている。" +
          "拠点の係数 k も実務値 " + K_FALLBACK + " を使用。"
      });
    }
    if (q6.length === 0) {
      notices.push({
        id: "no_conv",
        level: "warn",
        title: "当月の成約実績（q6）が空",
        body: "当月の成約が 1 件も取れていないため、成約ペースの既定値は 0、② も 0 になる。"
      });
    }
    var negFuture = hist.filter(function (h) { return h.isFuture && (h.lks < 0 || h.mny < 0 || h.pd < 0); });
    if (negFuture.length) {
      notices.push({
        id: "negative_future",
        level: "info",
        title: "未来月にマイナス計上がある",
        body: negFuture.length + " ヶ月（" + negFuture[0].ym + " 以降）で先行計上額がマイナス。" +
          "返金・取消の戻し計上によるもので、当月の着地見込みには影響しない。"
      });
    }

    return {
      meta: meta,
      inputs: {
        paceOn: paceOn, paceKyoten: paceKy, paceUnknown: paceUn,
        lagWeight: lagWeight,
        paceDefaults: paceDefaults,
        k: profile.k, kSource: profile.kSource
      },
      lks: lks,
      mny: mny,
      pd: pd,
      total: total,
      history: hist,
      checks: checks,
      notices: notices,
      targets: targets,
      conv: {
        online: convProgressOnline,
        kyoten: convProgressKyoten,
        hasPlan: convProgressOnline.hasPlan || convProgressKyoten.hasPlan
      },
      yomi: yomi,
      rowCounts: {
        q1_official_monthly: q1.length, q2_lks_pending: q2.length, q3_mny_pd_pending: q3.length,
        q4_lks_channel_booked: q4.length, q5_conv_profile: q5.length, q6_conv_actuals: q6.length,
        q7_conv_plan: q7.length, q8_yomi: q8.length
      }
    };
  }

  /* ------------------------------------------------------------------ *
   * 表示フォーマッタ（純関数・logic 側に置いてテストからも使う）
   * ------------------------------------------------------------------ */

  /** 百万円・小数1桁。単位記号 "M" 付き（チェック詳細など英字併記の文脈用）。 */
  function fmtMillion(v, digits) {
    var d = digits === undefined ? 1 : digits;
    if (v === null || v === undefined || !isFinite(v)) return "—";
    return (v / 1e6).toFixed(d) + "M";
  }

  /**
   * 百万円・小数1桁。単位記号なしの数値だけ。
   * 日本語の「百万円」を後ろに付ける文脈で使う（"370.3M 百万円" のような単位二重を避ける）。
   */
  function fmtMillionNum(v, digits) {
    var d = digits === undefined ? 1 : digits;
    if (v === null || v === undefined || !isFinite(v)) return "—";
    return (v / 1e6).toFixed(d);
  }

  /** 円・3桁区切り（詳細表）。 */
  function fmtYen(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    var n = Math.round(v);
    var neg = n < 0;
    var s = String(Math.abs(n));
    var out = "";
    while (s.length > 3) { out = "," + s.slice(-3) + out; s = s.slice(0, -3); }
    return (neg ? "-" : "") + s + out;
  }

  function fmtPct(v, digits) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    var d = digits === undefined ? 1 : digits;
    return (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
  }

  function fmtBytes(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    var units = ["B", "KB", "MB", "GB", "TB"], i = 0, n = v;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
  }

  return {
    // 主 API
    parseBqResult: parseBqResult,
    buildModel: buildModel,
    // 補助（UI / テスト用）
    CHANNELS: CHANNELS,
    CONV_CHANNELS: CONV_CHANNELS,
    QUERY_KEYS: QUERY_KEYS,
    K_FALLBACK: K_FALLBACK,
    DEFAULT_LAG_WEIGHT: DEFAULT_LAG_WEIGHT,
    MONTH_START_DOM: MONTH_START_DOM,
    DEFAULT_TARGETS: DEFAULT_TARGETS,
    SERVICE_KEYS: SERVICE_KEYS,
    normalizeTargets: normalizeTargets,
    targetDiff: targetDiff,
    convProgress: convProgress,
    rateOf: rateOf,
    buildProfile: buildProfile,
    defaultPaces: defaultPaces,
    lastDayOfMonth: lastDayOfMonth,
    prevMonth: prevMonth,
    fmtMillion: fmtMillion,
    fmtMillionNum: fmtMillionNum,
    fmtYen: fmtYen,
    fmtPct: fmtPct,
    fmtBytes: fmtBytes
  };
});
