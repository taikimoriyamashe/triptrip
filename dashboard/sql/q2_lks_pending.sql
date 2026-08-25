-- q2_lks_pending: LKS 既存会員の課金更新残(①window)・滞納/処理ラグ(④lag)をプラン×支払種別で集計。
-- 基準日 = CURRENT_DATE('Asia/Tokyo')。当月トークン(int_membership_tokens, order_status=1, 対象4プラン)を
--   effective_at < 基準日 → 'early' / それ以外 → 'window' に分け、order別の当月FA(int_likes_financial_accounting)と突合。
-- 単価: fa_per_day_cur = Σ early FA ÷ Σ early 当月内有効日数。fa_per_day_prev = 前月トークン全体の同計算
--   (月初で early が空 = fa_per_day_cur が NULL のときのフォールバック用。前月は全日 early 扱いなので全体で計算)。
-- 新規会員除外 [契約v1.1・R1裁定]: 当月に最初の有効成約をした user(likes_conversions rn=1 が当月)の order は
--   window(①: n_window/window_days)と lag(④: n_lag/lag_days)の両方から除外する。
--   ①④=既存会員のみ / ②③(q5×q6)=当月新規のみ、という対称な切り分けで二重計上を防止。
-- 防御 [契約v1.1]: all_orders は JOIN 前に order_id で1行化(重複ファンアウト防止)。
--   m_days は COALESCE(GREATEST(...,0),0) で NULL を 0 に正規化(COUNTIF/SUM の非対称防止)。
-- 出力列: plan_name STRING / payment_type STRING / fa_per_day_cur FLOAT(NULL可) / fa_per_day_prev FLOAT(NULL可)
--         / n_window INT(window かつ FA=0 の件数) / window_days INT / n_lag INT(early かつ FA=0 の件数) / lag_days INT
WITH params AS (
  SELECT basis_date,
    DATE_TRUNC(basis_date, MONTH) AS m_start,
    LAST_DAY(basis_date, MONTH) AS m_end,
    DATE_SUB(DATE_TRUNC(basis_date, MONTH), INTERVAL 1 MONTH) AS pm_start,
    DATE_SUB(DATE_TRUNC(basis_date, MONTH), INTERVAL 1 DAY) AS pm_end
  FROM (SELECT CURRENT_DATE('Asia/Tokyo') AS basis_date)
),
orders AS (
  -- order_id で1行化(all_orders の重複行による将来ファンアウト防止。MAXで決定的に代表値を選ぶ)
  SELECT order_id, MAX(payment_type) AS payment_type, MAX(user_id) AS user_id
  FROM `shelikes-001.sheinc_marts_accounting.all_orders`
  GROUP BY order_id
),
new_users AS (
  SELECT e.user_id
  FROM (
    SELECT user_id, DATE(purchased_at) AS pdate,
      ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY purchased_at ASC) AS rn
    FROM `shelikes-001.sheinc_marts_fy24.likes_conversions`
    WHERE is_valid_conversions IS TRUE
  ) e
  CROSS JOIN params p
  WHERE e.rn = 1 AND e.pdate >= p.m_start
),
tok_cur AS (
  SELECT t.order_id, t.plan_name, o.payment_type,
    COALESCE(GREATEST(DATE_DIFF(LEAST(t.expires_at, p.m_end), GREATEST(t.effective_at, p.m_start), DAY) + 1, 0), 0) AS m_days,
    IF(t.effective_at < p.basis_date, 'early', 'window') AS grp,
    (nu.user_id IS NOT NULL) AS is_new_user
  FROM `shelikes-001.sheinc_intermediate.int_membership_tokens` t
  JOIN orders o USING (order_id)
  CROSS JOIN params p
  LEFT JOIN new_users nu ON o.user_id = nu.user_id
  WHERE t.target_month = p.m_start
    AND t.order_status = 1
    AND t.plan_name IN ('レギュラープラン','スタンダードプラン','ライトプラン','卒業生プラン')
),
fa_cur AS (
  SELECT f.order_id,
    SUM(f.monthly_accounting_without_tax + f.monthly_accounting_discount + f.monthly_accounting_own_expense) AS m_fa
  FROM `shelikes-001.sheinc_intermediate.int_likes_financial_accounting` f
  CROSS JOIN params p
  WHERE f.target_month = p.m_start
  GROUP BY f.order_id
),
j_cur AS (
  SELECT t.*, COALESCE(f.m_fa, 0) AS m_fa
  FROM tok_cur t LEFT JOIN fa_cur f USING (order_id)
),
cur_agg AS (
  SELECT plan_name, payment_type,
    SAFE_DIVIDE(SUM(IF(grp='early', m_fa, 0)), NULLIF(SUM(IF(grp='early', m_days, 0)), 0)) AS fa_per_day_cur,
    COUNTIF(grp='window' AND m_fa = 0 AND NOT is_new_user) AS n_window,
    SUM(IF(grp='window' AND m_fa = 0 AND NOT is_new_user, m_days, 0)) AS window_days,
    COUNTIF(grp='early' AND m_fa = 0 AND NOT is_new_user) AS n_lag,
    SUM(IF(grp='early' AND m_fa = 0 AND NOT is_new_user, m_days, 0)) AS lag_days
  FROM j_cur
  GROUP BY 1, 2
),
tok_prev AS (
  SELECT t.order_id, t.plan_name, o.payment_type,
    COALESCE(GREATEST(DATE_DIFF(LEAST(t.expires_at, p.pm_end), GREATEST(t.effective_at, p.pm_start), DAY) + 1, 0), 0) AS m_days
  FROM `shelikes-001.sheinc_intermediate.int_membership_tokens` t
  JOIN orders o USING (order_id)
  CROSS JOIN params p
  WHERE t.target_month = p.pm_start
    AND t.order_status = 1
    AND t.plan_name IN ('レギュラープラン','スタンダードプラン','ライトプラン','卒業生プラン')
),
fa_prev AS (
  SELECT f.order_id,
    SUM(f.monthly_accounting_without_tax + f.monthly_accounting_discount + f.monthly_accounting_own_expense) AS m_fa
  FROM `shelikes-001.sheinc_intermediate.int_likes_financial_accounting` f
  CROSS JOIN params p
  WHERE f.target_month = p.pm_start
  GROUP BY f.order_id
),
prev_agg AS (
  SELECT t.plan_name, t.payment_type,
    SAFE_DIVIDE(SUM(COALESCE(f.m_fa, 0)), NULLIF(SUM(t.m_days), 0)) AS fa_per_day_prev
  FROM tok_prev t LEFT JOIN fa_prev f USING (order_id)
  GROUP BY 1, 2
)
SELECT
  c.plan_name,
  c.payment_type,
  c.fa_per_day_cur,
  p.fa_per_day_prev,
  c.n_window,
  c.window_days,
  c.n_lag,
  c.lag_days
FROM cur_agg c
LEFT JOIN prev_agg p USING (plan_name, payment_type)
ORDER BY c.plan_name, c.payment_type
