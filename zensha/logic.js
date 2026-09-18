/*
 * logic.js — 全社着地モニターの「計算とルール文生成」。DOM に触れない純関数だけを置く。
 *
 *   ブラウザ : build.py が template.html の /*__LOGIC__*\/ にそのまま差し込む。
 *   Node     : require("./logic.js") で同じ API が取れる（test_build.js 用）。
 *
 * 丸め・単位は原本 (reference/original-2026-09-08.html) に合わせる:
 *   億円2桁 / 百万円1桁 / %1桁 / 件は整数。
 */
var ZENSHA_LOGIC = (function () {
  "use strict";

  /* ================================================================
   * 1. フォーマット
   * ================================================================ */
  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function n0(n) { return Math.round(n).toLocaleString("ja-JP"); }
  function yen(n) { return "¥" + Math.round(n).toLocaleString("ja-JP"); }
  function mil(n) { return (n / 1000000).toFixed(1); }                       // 百万円1桁
  function milR(n) { return Math.round(n / 1000000).toLocaleString("ja-JP"); } // 百万円（整数）
  function oku(n) { return (n / 100000000).toFixed(2); }                     // 億円2桁
  function pct(n) { return n.toFixed(1) + "%"; }
  function pct0(n) { return Math.round(n) + "%"; }
  function milM(n) { return "¥" + (n / 1000000).toFixed(1) + "M"; }          // ¥26.5M

  /** 符号つき。plus="+"、minus="▲"（原本の表記）。 */
  function signed(n, fmt) { return (n >= 0 ? "+" : "▲") + fmt(Math.abs(n)); }
  function signedN(n) { return signed(n, n0); }
  function signedMilR(n) { return (n >= 0 ? "+¥" : "▲¥") + milR(Math.abs(n)) + "百万"; }

  function sum(a) {
    return (a || []).reduce(function (x, y) { return x + (isNum(y) ? y : 0); }, 0);
  }

  /* ================================================================
   * 2. 判定（原本の tone / verdict と同一）
   * ================================================================ */
  function tone(r) { return r >= 100 ? "g" : (r >= 90 ? "w" : "b"); }

  function verdict(r) {
    if (r >= 100) return { cls: "is-good", main: "達成見込み", sub: "" };
    if (r >= 90) return { cls: "is-warn", main: "未達見込み", sub: "要テコ入れ" };
    return { cls: "is-bad", main: "未達見込み", sub: "未達リスク大" };
  }
  /** バッジに出る一番細かいラベル（達成見込み / 要テコ入れ / 未達リスク大）。 */
  function verdictLabel(r) { var v = verdict(r); return v.sub || v.main; }

  /* ================================================================
   * 3. 日付・月
   * ================================================================ */
  function parseYM(s) { var p = String(s).split("-"); return { y: +p[0], m: +p[1] }; }
  function monthNo(ym) { return parseYM(ym).m; }
  function prevMonthNo(ym) { var m = parseYM(ym).m; return m === 1 ? 12 : m - 1; }
  /** "2026-09-07" → "9/7" */
  function md(s) { var p = String(s).split("-"); return (+p[1]) + "/" + (+p[2]); }
  function utc(s) { var p = String(s).split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  /** a - b を日数で。 */
  function daysDiff(a, b) { return Math.round((utc(a) - utc(b)) / 86400000); }
  function todayISO(d) {
    d = d || new Date();
    function z(x) { return (x < 10 ? "0" : "") + x; }
    return d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate());
  }
  /** ["2026-04",…] → ["4","5",…]（チャートの軸ラベル） */
  function monthLabels(months) { return (months || []).map(function (s) { return String(parseYM(s).m); }); }

  /**
   * 「n日前のデータ」バッジ。
   *  - basis_date が今日なら出さない
   *  - (今日 − data_through) が 2日未満なら出さない（前日までのデータは正常）
   *  - n は 今日 − basis_date（原本の「2日前のデータ」＝ 9/10 − 9/8 と同じ数え方）
   */
  function staleBadge(basisDate, dataThrough, today) {
    today = today || todayISO();
    if (!basisDate || !dataThrough) return null;
    if (basisDate === today) return null;
    if (daysDiff(today, dataThrough) < 2) return null;
    var n = daysDiff(today, basisDate);
    if (n < 1) return null;
    return n + "日前のデータ";
  }

  /** ヘッダの日付行。 */
  function stampParts(D, today) {
    var m = monthNo(D.target_month);
    return {
      basis: "データ取得 <b>" + D.basis_date + "</b>（実績は " + md(D.data_through) + " まで）",
      month: m + "月 <b>経過" + D.elapsed_days + "日 / " + D.days_in_month + "日・残" + D.remaining_days + "日</b>",
      stale: staleBadge(D.basis_date, D.data_through, today)
    };
  }

  /** 「4〜8月は実績、9月以降はAヨミ」 */
  function actualSpanNote(months, actualUntilIndex) {
    var lab = monthLabels(months);
    if (!actualUntilIndex) return "全月がAヨミ。棒の上は「計画との差（百万円）」";
    var first = lab[0], last = lab[Math.max(0, actualUntilIndex - 1)];
    var next = lab[actualUntilIndex];
    var head = (actualUntilIndex === 1 ? (first + "月は実績") : (first + "〜" + last + "月は実績"));
    return head + (next ? "、" + next + "月以降はAヨミ" : "");
  }

  /* ================================================================
   * 4. ペース計算（原本の式をそのまま）
   * ================================================================ */
  /** 実績ペース外挿 = act / elapsed × days（件は整数） */
  function paceExtrap(act, elapsed, days) {
    if (!elapsed || elapsed <= 0) return null;
    return Math.round(act / elapsed * days);
  }
  /** 日割計画 = target × elapsed / days */
  function planToDate(target, elapsed, days) {
    if (!days) return 0;
    return Math.round(target * elapsed / days);
  }
  /** 必要ペース = (target − act) / remaining。残0日なら null。 */
  function neededPace(target, act, remaining) {
    if (!remaining || remaining <= 0) return null;
    return (target - act) / remaining;
  }
  /** 計画ペース = target / days */
  function planPace(target, days) { return days ? target / days : 0; }

  /** 日次配列の合計・平均（null は除外）。 */
  function dailyStats(v) {
    var s = 0, n = 0;
    (v || []).forEach(function (x) { if (isNum(x)) { s += x; n++; } });
    return { sum: s, n: n, len: (v || []).length, avg: n > 0 ? s / n : null };
  }
  /** 直近実績 = 直近7日平均（7日に満たなければ全日平均）。null は除外。 */
  function recentPace(v, win) {
    win = win || 7;
    var a = (v || []).filter(isNum);
    if (!a.length) return null;
    var t = a.slice(Math.max(0, a.length - win));
    return t.reduce(function (x, y) { return x + y; }, 0) / t.length;
  }

  /**
   * レーン（オンライン／拠点）の全指標。
   *  o = {target, yomi, act, daily(配列), elapsed, days, remaining, recent?}
   */
  function laneModel(o) {
    var target = o.target, days = o.days, elapsed = o.elapsed, remaining = o.remaining;
    var pace = isNum(o.pace) ? o.pace : paceExtrap(o.act, elapsed, days);
    var ry = target > 0 ? o.yomi / target * 100 : 0;
    var rp = (target > 0 && pace !== null) ? pace / target * 100 : 0;
    var worst = Math.min(ry, rp);
    var ptd = planToDate(target, elapsed, days);
    var need = neededPace(target, o.act, remaining);
    var rec = isNum(o.recent) ? o.recent : recentPace(o.daily);
    return {
      target: target, yomi: o.yomi, act: o.act, pace: pace,
      yomiRate: ry, paceRate: rp, worst: worst,
      yomiGap: o.yomi - target,
      paceGap: pace === null ? null : pace - target,
      planToDate: ptd, behind: o.act - ptd,
      actRate: target > 0 ? o.act / target * 100 : 0,
      dateRate: days > 0 ? elapsed / days * 100 : 0,
      need: need, planPace: planPace(target, days), recent: rec,
      needRatio: (need !== null && rec) ? need / rec : null,
      verdict: verdict(worst), tone: tone(worst),
      forecastGap: pace === null ? null : (o.yomi - pace)
    };
  }

  /* ================================================================
   * 5. 段階（STEP）
   * ================================================================ */
  function stepBy(step, name) {
    for (var i = 0; i < (step || []).length; i++) if (step[i].n === name) return step[i];
    return null;
  }
  function keyStep(step) {
    for (var i = 0; i < (step || []).length; i++) if (step[i].key) return step[i];
    return null;
  }
  function stepFmt(r, v) {
    if (r.unit === "%") return v.toFixed(1) + "%";
    if (r.unit === "¥") return yen(v);
    return n0(v);
  }
  /** 段階テーブル1行の派生値（原本 steps() と同じ）。 */
  function stepRow(r, dayRatio) {
    var isRate = !!r.unit;
    var ratio = r.lowerBetter ? (r.p / r.y * 100) : (r.y / r.p * 100);
    var d = r.y - r.p;
    var dTone = r.lowerBetter ? (d <= 0 ? "g" : "b") : (d >= 0 ? "g" : "b");
    var dTxt;
    if (r.unit === "%") dTxt = (d >= 0 ? "+" : "▲") + Math.abs(d).toFixed(1) + "pt";
    else if (r.unit === "¥") dTxt = (d >= 0 ? "+" : "▲") + yen(Math.abs(d)).slice(1);
    else dTxt = (d >= 0 ? "+" : "▲") + n0(Math.abs(d));
    var gap = isRate ? null : (r.a - r.p * dayRatio);
    return {
      isRate: isRate, ratio: ratio, tone: tone(ratio), diff: d, diffTone: dTone, diffText: dTxt,
      gap: gap, gapText: gap === null ? "―" : ((gap >= 0 ? "+" : "▲") + n0(Math.abs(gap))),
      gapTone: gap === null ? "muted" : (gap >= 0 ? "g" : "b"),
      p: stepFmt(r, r.p), y: stepFmt(r, r.y), a: stepFmt(r, r.a)
    };
  }

  /* ================================================================
   * 6. ギャップの主因
   * ================================================================ */
  /**
   * ①参加要因 = (参加ヨミ − 参加計画) × 計画成約率
   * ②転換要因 = 参加ヨミ × (ヨミ成約率 − 計画成約率)
   */
  function causeImpacts(step) {
    var at = stepBy(step, "参加"), cv = stepBy(step, "成約率");
    if (!at || !cv) return { attend: 0, conv: 0 };
    return {
      attend: Math.round((at.y - at.p) * (cv.p / 100)),
      conv: Math.round(at.y * ((cv.y - cv.p) / 100))
    };
  }

  var CIRCLED = ["①", "②", "③", "④"];

  /**
   * 主因カード3枚。kind="online" → [参加, 転換, 予測差] / kind="kyoten" → [参加, CPA, 転換]
   * cost = {plan, act} （拠点の広告費。無ければ CPA カードは広告費行を省く）
   */
  function buildCauses(kind, step, lane, cost) {
    var imp = causeImpacts(step);
    var ap = stepBy(step, "申込"), bk = stepBy(step, "予約"), at = stepBy(step, "参加");
    var cv = stepBy(step, "成約率"), jr = stepBy(step, "参加率"), cpa = stepBy(step, "CPA");
    var key = keyStep(step) || { p: lane.target, y: lane.yomi, a: lane.act };
    var diff = key.y - key.p;
    var cards = [];

    /* --- 参加要因 --- */
    var attendCard;
    if (kind === "kyoten") {
      var ratioApply = (ap && ap.p) ? ap.y / ap.p * 100 : 100;
      var share = Math.abs(diff) > 0 ? Math.abs(imp.attend) / Math.abs(diff) : 0;
      var howMuch = share >= 0.8 ? "のほぼ全部" : (share >= 0.5 ? "の大半" : "の一部");
      attendCard = {
        kind: (Math.abs(imp.attend) >= Math.abs(imp.conv) ? CIRCLED[0] + " 最大要因"
          : CIRCLED[0] + (imp.attend >= 0 ? " 押し上げ" : " 押し下げ")),
        lab: "成約数への影響", v: signedN(imp.attend) + "件", tone: imp.attend >= 0 ? "g" : "b",
        h: ratioApply < 100
          ? "申込が計画の" + Math.round(ratioApply / 10) + "割しか集まっていない"
          : "申込が計画を上回っている",
        p: (ap ? "申込" + n0(ap.p) + "→" + n0(ap.y) + "、" : "")
          + (at ? "参加" + n0(at.p) + "→" + n0(at.y) + "。" : "")
          + "ここが拠点の" + signedN(diff) + "件" + howMuch + "。",
        dl: []
      };
      if (ap) attendCard.dl.push(["申込", n0(ap.p) + " → <b>" + n0(ap.y) + "</b>（" + Math.round(ratioApply) + "%）"]);
      if (at) attendCard.dl.push(["参加", n0(at.p) + " → <b>" + n0(at.y) + "</b>"]);
    } else {
      var ups = [];
      if (ap) ups.push({ n: "申込", up: ap.y >= ap.p });
      if (bk) ups.push({ n: "予約", up: bk.y >= bk.p });
      var allUp = ups.length > 0 && ups.every(function (x) { return x.up; });
      var allDown = ups.length > 0 && ups.every(function (x) { return !x.up; });
      var names = ups.map(function (x) { return x.n; }).join("・");
      var lead = ups.length === 0 ? ""
        : (allUp ? names + "とも計画超で、" : (allDown ? names + "とも計画割れで、" : names + "は計画とまちまちで、"));
      attendCard = {
        kind: CIRCLED[0] + (imp.attend >= 0 ? " 押し上げ" : " 押し下げ"),
        lab: "成約数への影響", v: signedN(imp.attend) + "件", tone: imp.attend >= 0 ? "g" : "b",
        h: imp.attend >= 0 ? "参加数が計画を上回っている" : "参加数が計画を下回っている",
        p: lead + (at ? "参加が" + n0(at.y) + "（計画" + n0(at.p) + "）。" : "")
          + (cpa ? (cpa.y <= cpa.p ? "CPAも目標より安い。" : "CPAは目標より高い。") : ""),
        dl: []
      };
      if (at) attendCard.dl.push(["参加", n0(at.p) + " → <b>" + n0(at.y) + "</b>"]);
      if (cpa) attendCard.dl.push(["CPA", yen(cpa.p) + " → <b>" + yen(cpa.y) + "</b>"]);
    }

    /* --- 転換要因 --- */
    var convCard = {
      lab: "成約数への影響", v: signedN(imp.conv) + "件", tone: imp.conv >= 0 ? "g" : "b",
      dl: []
    };
    if (kind === "kyoten") {
      convCard.kind = CIRCLED[2] + (imp.conv >= 0 ? " 問題ではない所" : " 押し下げ");
      convCard.h = imp.conv >= 0 ? "現場の転換力は計画どおり" : "参加 → 成約の転換率が落ちている";
      convCard.p = imp.conv >= 0
        ? "参加してからの成約率はむしろ計画を上回る。直すべきは入口であって、拠点の接客・クロージングではない。"
        : "参加してからの成約率が計画を下回っている。入口だけでなく、当日の転換も見る必要がある。";
      if (cv) convCard.dl.push(["成約率", cv.p.toFixed(1) + "% → <b>" + cv.y.toFixed(1) + "%</b>"]);
      if (jr) convCard.dl.push(["参加率", jr.p.toFixed(1) + "% → <b>" + jr.y.toFixed(1) + "%</b>"]);
    } else {
      convCard.kind = CIRCLED[1] + (imp.conv >= 0 ? " 押し上げ" : " 押し下げ");
      convCard.h = imp.conv >= 0 ? "参加 → 成約の転換率が上がっている" : "参加 → 成約の転換率が落ちている";
      if (imp.conv < 0 && imp.attend > 0) {
        convCard.p = "参加が増えた分を、成約率の低下が食っている。集客の質か、当日の転換かの切り分けが要る。";
      } else if (imp.conv < 0) {
        convCard.p = "参加が減っているうえに、成約率まで計画を下回っている。集客の質か、当日の転換かの切り分けが要る。";
      } else {
        convCard.p = "成約率が計画を上回り、参加側の不足を補っている。";
      }
      if (cv) {
        var dpt = cv.y - cv.p;
        convCard.dl.push(["成約率", cv.p.toFixed(1) + "% → <b>" + cv.y.toFixed(1) + "%</b>（<span class=\""
          + (dpt >= 0 ? "g" : "b") + "\">" + (dpt >= 0 ? "+" : "▲") + Math.abs(dpt).toFixed(1) + "pt</span>）"]);
      }
    }

    /* --- CPA（拠点） --- */
    var cpaCard = null;
    if (kind === "kyoten" && cpa) {
      var cr = cpa.p ? cpa.y / cpa.p : 1;
      var spendRate = (cost && cost.plan) ? (cost.act || 0) / cost.plan * 100 : null;
      var worseCpa = cr > 1;
      var p1 = spendRate === null ? ""
        : (spendRate < 90
          ? "広告費は" + Math.round(spendRate) + "%しか消化しておらず予算は余っている。"
          : "広告費は" + Math.round(spendRate) + "%まで消化しており予算の余りは少ない。");
      var p2 = worseCpa
        ? "ただし単価が計画の" + cr.toFixed(1) + "倍で、出せば出すほどCACが悪化する。"
        : "単価は計画の" + cr.toFixed(1) + "倍に収まっており、増額の余地がある。";
      cpaCard = {
        kind: (worseCpa && imp.attend < 0) ? CIRCLED[1] + " ①が起きている原因" : CIRCLED[1] + " 申込単価",
        lab: "申込CPA", v: cr.toFixed(2) + "倍", tone: cr > 1.05 ? "b" : (cr < 0.95 ? "g" : "w"),
        h: worseCpa ? "同じ予算で申込が取れなくなっている" : "同じ予算で申込が多く取れている",
        p: p1 + p2,
        dl: [["CPA", yen(cpa.p) + " → <b>" + yen(cpa.y) + "</b>"]]
      };
      if (cost && cost.plan) cpaCard.dl.push(["広告費", milM(cost.act || 0) + " / " + milM(cost.plan)]);
    }

    /* --- 2つの予測が割れる理由（オンライン） --- */
    var fcCard = null;
    if (kind !== "kyoten") {
      var gap = lane.forecastGap === null ? 0 : lane.forecastGap;
      fcCard = {
        kind: CIRCLED[2] + " 2つの予測が割れる理由",
        lab: "Aヨミとペースの差", v: n0(Math.abs(gap)) + "件", tone: "w",
        h: (cv && cv.a < cv.y) ? "実績の成約率が、Aヨミの前提に届いていない"
          : "実績の成約率が、Aヨミの前提を上回っている",
        p: cv
          ? ("Aヨミは" + cv.y.toFixed(1) + "%を前提にしているが、" + (lane.throughLabel || "")
            + "までの実績は" + cv.a.toFixed(1) + "%。この差が「Aヨミ" + n0(lane.yomi) + "」と「ペース"
            + n0(lane.pace) + "」の開き。①②とは別軸の数字。")
          : ("「Aヨミ" + n0(lane.yomi) + "」と「ペース" + n0(lane.pace) + "」の開き。①②とは別軸の数字。"),
        dl: []
      };
      if (cv && at && key) fcCard.dl.push(["実績", cv.a.toFixed(1) + "%（" + n0(key.a) + " / " + n0(at.a) + "）"]);
      if (cv) fcCard.dl.push(["Aヨミ前提", cv.y.toFixed(1) + "%"]);
    }

    if (kind === "kyoten") {
      cards = [attendCard];
      if (cpaCard) cards.push(cpaCard);
      cards.push(convCard);
    } else {
      cards = [attendCard, convCard];
      if (fcCard) cards.push(fcCard);
    }
    return cards;
  }

  /** 「月計画868 → Aヨミ887（差 +19件 ＝ ①＋②）」 */
  function causeSummary(step, cards) {
    var key = keyStep(step);
    if (!key) return "";
    var idx = [];
    cards.forEach(function (c, i) { if (c.lab === "成約数への影響") idx.push(CIRCLED[i]); });
    var d = key.y - key.p;
    return "月計画" + n0(key.p) + " → Aヨミ" + n0(key.y)
      + "（差 " + signedN(d) + "件"
      + (idx.length ? " ＝ " + idx.join("＋") : "") + "）";
  }

  /* ================================================================
   * 7. 今月のトピックス 01–04（ルール生成）
   * ================================================================ */
  /** 通期のサービス別 着地差（大きく未達な順）。 */
  function fyBreakdown(rev, services) {
    return services.map(function (s) {
      var d = rev[s.key];
      var p = sum(d.plan), a = sum(d.act);
      return { key: s.key, name: s.short || s.name, plan: p, act: a, diff: a - p, rate: p ? a / p * 100 : 0 };
    });
  }

  function topic01(rev, services) {
    var rows = fyBreakdown(rev, services);
    var tp = sum(rev.total.plan), ta = sum(rev.total.act);
    var totalDiff = ta - tp;
    var sorted = rows.slice().sort(function (a, b) { return a.diff - b.diff; });

    if (totalDiff >= 0) {
      var best = sorted.slice().reverse().slice(0, 2);
      return {
        h: "通期は計画を" + signedMilR(totalDiff).replace("+", "") + "上回る見通し",
        p: best.map(function (r) {
          return r.name + " <b class=\"num\">" + signedMilR(r.diff) + "</b>（" + pct(r.rate) + "）";
        }).join("、") + " が牽引。全社の通期着地は <b class=\"num\">" + pct(tp ? ta / tp * 100 : 0) + "</b>。"
      };
    }

    var top2 = sorted.filter(function (r) { return r.diff < 0; }).slice(0, 2);
    if (!top2.length) top2 = sorted.slice(0, 2);
    // 選ぶのは未達額の大きい順、並べるのはサービス定義順（原本の並び）
    top2.sort(function (a, b) {
      return rows.indexOf(a) - rows.indexOf(b);
    });
    var top2Diff = top2.reduce(function (x, r) { return x + r.diff; }, 0);
    var restRows = rows.filter(function (r) {
      return !top2.some(function (t) { return t.key === r.key; });
    });
    var restDiff = restRows.reduce(function (x, r) { return x + r.diff; }, 0);
    var share = totalDiff ? Math.round(top2Diff / totalDiff * 100) : 0;
    var restNames = restRows.map(function (r) { return r.name; }).join("・");
    var restTxt = restDiff < 0
      ? restNames + "の" + restRows.length + "サービスは合計でも <b class=\"num\">" + signedMilR(restDiff) + "</b> に収まっており、"
      : restNames + "の" + restRows.length + "サービスは合計で <b class=\"num\">" + signedMilR(restDiff) + "</b> の超過で、";
    var closing = Math.abs(restDiff) < Math.abs(top2Diff)
      ? "<b>全社の未達は主力サービスの問題ではない。</b>"
      : "<b>未達は複数のサービスに広がっている。</b>";
    var names2 = top2.map(function (r) { return r.name; }).join("と");
    // 他サービスが超過していると寄与率が100%を超えるので、その時は率を出さない
    var head = share >= 100
      ? "通期" + signedMilR(totalDiff) + "は、" + names2 + "の" + signedMilR(top2Diff) + "が丸ごと効いている"
      : "通期" + signedMilR(totalDiff) + "の" + share + "%は、" + names2 + "の2つ";
    return {
      h: head,
      p: top2.map(function (r, i) {
        return r.name + " <b class=\"num\">" + signedMilR(r.diff) + "</b>（"
          + (i === 0 ? "達成率" : "") + pct(r.rate) + "）";
      }).join("、")
        + " で合計 <b class=\"num\">" + signedMilR(top2Diff) + "</b>。" + restTxt + closing
    };
  }

  function topic02(on, kp, onStep, kpStep) {
    var onOver = on.yomiRate >= 100, kpOver = kp.yomiRate >= 100;
    var h;
    if (onOver && !kpOver) h = "SHElikes の{M}月成約は、拠点だけが落ちている";
    else if (!onOver && kpOver) h = "SHElikes の{M}月成約は、オンラインだけが落ちている";
    else if (onOver && kpOver) h = "SHElikes の{M}月成約は、オンライン・拠点とも目標超え";
    else h = "SHElikes の{M}月成約は、オンラインも拠点も届いていない";

    var weakIsKp = kp.yomiRate <= on.yomiRate;
    var weak = weakIsKp ? kp : on;
    var weakStep = weakIsKp ? kpStep : onStep;
    var weakName = weakIsKp ? "拠点" : "オンライン";
    var cv = stepBy(weakStep, "成約率"), ap = stepBy(weakStep, "申込");
    var tail;
    if (!cv || !ap) {
      tail = "";
    } else if (cv.y >= cv.p && ap.y < ap.p) {
      tail = weakName + "は成約率が計画どおり（" + cv.y.toFixed(1) + "% vs " + cv.p.toFixed(1)
        + "%）で、<b>足りないのは申込</b>（計画" + n0(ap.p) + " → 見込み" + n0(ap.y) + "）。";
    } else if (cv.y < cv.p && ap.y >= ap.p) {
      tail = weakName + "は申込が計画どおり（" + n0(ap.y) + " vs " + n0(ap.p)
        + "）で、<b>足りないのは転換</b>（成約率 " + cv.p.toFixed(1) + "% → " + cv.y.toFixed(1) + "%）。";
    } else if (cv.y < cv.p) {
      tail = weakName + "は<b>申込も成約率も計画割れ</b>（申込 " + n0(ap.p) + " → " + n0(ap.y)
        + "、成約率 " + cv.p.toFixed(1) + "% → " + cv.y.toFixed(1) + "%）。";
    } else {
      tail = weakName + "は申込・成約率とも計画を上回っている（申込 " + n0(ap.p) + " → " + n0(ap.y)
        + "、成約率 " + cv.p.toFixed(1) + "% → " + cv.y.toFixed(1) + "%）。";
    }
    return {
      h: h,
      p: "オンラインは月目標" + n0(on.target) + "に対しAヨミ <b class=\"num\">" + n0(on.yomi) + "件（"
        + pct(on.yomiRate) + "）</b> で" + (onOver ? "超過見込み" : "未達見込み") + "。"
        + "一方の拠点は目標" + n0(kp.target) + "に対し <b class=\"num\">" + n0(kp.yomi) + "件（"
        + pct(kp.yomiRate) + "）</b>。" + tail
    };
  }

  function topic03(kpStep, cost) {
    var cpa = stepBy(kpStep, "CPA");
    if (!cpa || !cost || !cost.plan) return null;
    var cr = cpa.p ? cpa.y / cpa.p : 1;
    var spendRate = (cost.act || 0) / cost.plan * 100;
    var loose = spendRate < 90;
    var h = cr > 1
      ? "拠点の広告予算は" + (loose ? "余っているが" : "ほぼ使い切りで") + "、単価が" + cr.toFixed(2) + "倍"
      : "拠点の広告単価は計画内（" + cr.toFixed(2) + "倍）、" + (loose ? "予算はまだ余っている" : "予算はほぼ消化");
    var tail = cr > 1
      ? "出せば申込は増えるが、そのぶん成約CACが悪化する。<b>増額の可否が今週の判断事項。</b>"
      : "単価は計画内のため、<b>消化を早めれば申込を積み増せる。</b>";
    return {
      h: h,
      p: "{M}月広告費は計画" + milM(cost.plan) + "に対し消化 <b class=\"num\">" + milM(cost.act || 0)
        + "（" + pct0(spendRate) + "）</b>。申込CPAは計画" + yen(cpa.p) + "に対し <b class=\"num\">"
        + yen(cpa.y) + "</b>。" + tail
    };
  }

  function topic04(lane, onLane, kpLane, onStep, kpStep) {
    var vy = verdictLabel(lane.yomiRate), vp = verdictLabel(lane.paceRate);
    var split = Math.abs(lane.forecastGap || 0) / (lane.target || 1) >= 0.03;
    var judge = (vy === vp)
      ? "どちらの見方でも判定は「" + vy + "」。"
      : "判定が「" + vy + "」と「" + vp + "」に分かれる水準。";
    var onGap = Math.abs(onLane.forecastGap || 0), kpGap = Math.abs(kpLane.forecastGap || 0);
    var src = onGap >= kpGap ? { name: "オンライン", step: onStep } : { name: "拠点", step: kpStep };
    var cv = stepBy(src.step, "成約率");
    var why = cv
      ? ("差の主因は" + src.name + "の実績成約率 <b class=\"num\">" + cv.a.toFixed(1)
        + "%</b> が、Aヨミ前提の <b class=\"num\">" + cv.y.toFixed(1) + "%</b> に"
        + (cv.a < cv.y ? "届いていないこと。" : "対して上振れていること。"))
      : "";
    var h = (vy !== vp)
      ? "SHElikes は2つの予測が割れている"
      : (split
        ? "SHElikes は2つの予測に" + n0(Math.abs(lane.forecastGap || 0)) + "件の開きがある"
        : "SHElikes の2つの予測はほぼ一致している");
    return {
      h: h,
      p: "Aヨミ <b class=\"num\">" + n0(lane.yomi) + "件（" + pct(lane.yomiRate) + "）</b> に対し、"
        + "実績ペースの外挿は <b class=\"num\">" + n0(lane.pace) + "件（" + pct(lane.paceRate) + "）</b>。"
        + judge + why
    };
  }

  /** 01–04 をまとめて。{M} は対象月に置換する。 */
  function buildTopics(ctx) {
    var m = monthNo(ctx.target_month);
    var list = [
      topic01(ctx.revenue, ctx.services),
      topic02(ctx.on, ctx.kp, ctx.onStep, ctx.kpStep),
      topic03(ctx.kpStep, ctx.kpCost),
      topic04(ctx.lks, ctx.on, ctx.kp, ctx.onStep, ctx.kpStep)
    ].filter(Boolean);
    return list.map(function (t, i) {
      return {
        n: (i + 1 < 10 ? "0" : "") + (i + 1),
        h: String(t.h).replace(/\{M\}/g, m),
        p: String(t.p).replace(/\{M\}/g, m)
      };
    });
  }

  /* ================================================================
   * 8. 現場で対応中（拠点CPA文）
   * ================================================================ */
  /** 「梅田・横浜はCPA改善が進行中（梅田 8月¥42,730→9月目標¥32,000、横浜 ¥44,613→¥31,000）。」 */
  function siteCpaSentence(sites, targetMonth) {
    var rows = (sites || []).filter(function (s) { return isNum(s.cpa_prev) && isNum(s.cpa_target); });
    if (!rows.length) return "";
    var pm = prevMonthNo(targetMonth), tm = monthNo(targetMonth);
    var names = rows.map(function (s) { return s.name; }).join("・");
    var detail = rows.map(function (s, i) {
      var head = s.name + " ";
      var from = (i === 0 ? pm + "月" : "") + yen(s.cpa_prev);
      var to = (i === 0 ? tm + "月目標" : "") + yen(s.cpa_target);
      return head + from + "→" + to;
    }).join("、");
    return names + "はCPA改善が進行中（" + detail + "）。";
  }

  /* ================================================================
   * 9. 入力チェック（build.py と同じ必須キー）
   * ================================================================ */
  var REQUIRED_LATEST = [
    "basis_date", "data_through", "target_month",
    "days_in_month", "elapsed_days", "remaining_days",
    "fy", "actual_until_index", "revenue", "lks", "sources"
  ];

  return {
    isNum: isNum, n0: n0, yen: yen, mil: mil, milR: milR, oku: oku,
    pct: pct, pct0: pct0, milM: milM, signed: signed, signedN: signedN,
    signedMilR: signedMilR, sum: sum,
    tone: tone, verdict: verdict, verdictLabel: verdictLabel,
    parseYM: parseYM, monthNo: monthNo, prevMonthNo: prevMonthNo, md: md,
    daysDiff: daysDiff, todayISO: todayISO, monthLabels: monthLabels,
    staleBadge: staleBadge, stampParts: stampParts, actualSpanNote: actualSpanNote,
    paceExtrap: paceExtrap, planToDate: planToDate, neededPace: neededPace,
    planPace: planPace, dailyStats: dailyStats, recentPace: recentPace, laneModel: laneModel,
    stepBy: stepBy, keyStep: keyStep, stepFmt: stepFmt, stepRow: stepRow,
    causeImpacts: causeImpacts, buildCauses: buildCauses, causeSummary: causeSummary,
    fyBreakdown: fyBreakdown, topic01: topic01, topic02: topic02, topic03: topic03,
    topic04: topic04, buildTopics: buildTopics, siteCpaSentence: siteCpaSentence,
    REQUIRED_LATEST: REQUIRED_LATEST
  };
})();
if (typeof module !== "undefined" && module.exports) { module.exports = ZENSHA_LOGIC; }
