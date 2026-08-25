-- q3_mny_pd_pending: MNY(money)・プロデ(multicreator)の課金更新残・滞納/処理ラグを service_key 別に集計。
-- q2 と同じ手法(基準日 = CURRENT_DATE('Asia/Tokyo')、early/window 分割、1日単価×残日数)。
-- FA は sheinc_marts_accounting.monthly_accounting(order_id別 当月SUM: without_tax + discount + own_expense)。
-- 既知の仕様: multicreator の FA は marts monthly_accounting に載らない別パイプライン
--   → fa_per_day_cur/prev は 0 または NULL になる(正しい挙動)。件数列(n_window, n_active_*)だけ意味を持つ。
-- 新規会員除外は行わない [契約v1.1]: MNY/プロデには②③(新規成約プロファイル成分)が存在しないため。
-- 防御 [契約v1.1]: all_orders は JOIN 前に order_id で1行化(重複ファンアウト防止)。
--   m_days は COALESCE(GREATEST(...,0),0) で NULL を 0 に正規化(COUNTIF/SUM の非対称防止)。
-- 出力列: service_key STRING / fa_per_day_cur FLOAT(NULL可) / fa_per_day_prev FLOAT(NULL可)
--         / n_window INT / window_days INT / n_lag INT / lag_days INT
--         / n_active_cur INT(当月トークン件数) / n_active_prev INT(前月トークン件数)
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
  SELECT order_id, MAX(service_key) AS service_key
  FROM `shelikes-001.sheinc_marts_accounting.all_orders`
  GROUP BY order_id
),
tok_cur AS (
  SELECT t.order_id, o.service_key,
    COALESCE(GREATEST(DATE_DIFF(LEAST(t.expires_at, p.m_end), GREATEST(t.effective_at, p.m_start), DAY) + 1, 0), 0) AS m_days,
    IF(t.effective_at < p.basis_date, 'early', 'window') AS grp
  FROM `shelikes-001.sheinc_intermediate.int_membership_tokens` t
  JOIN orders o USING (order_id)
  CROSS JOIN params p
  WHERE t.target_month = p.m_start
    AND t.order_status = 1
    AND o.service_key IN ('money', 'multicreator')
),
fa_cur AS (
  SELECT f.order_id,
    SUM(f.monthly_accounting_without_tax + f.monthly_accounting_discount + f.monthly_accounting_own_expense) AS m_fa
  FROM `shelikes-001.sheinc_marts_accounting.monthly_accounting` f
  CROSS JOIN params p
  WHERE f.target_month = p.m_start
  GROUP BY f.order_id
),
j_cur AS (
  SELECT t.*, COALESCE(f.m_fa, 0) AS m_fa
  FROM tok_cur t LEFT JOIN fa_cur f USING (order_id)
),
cur_agg AS (
  SELECT service_key,
    SAFE_DIVIDE(SUM(IF(grp='early', m_fa, 0)), NULLIF(SUM(IF(grp='early', m_days, 0)), 0)) AS fa_per_day_cur,
    COUNTIF(grp='window' AND m_fa = 0) AS n_window,
    SUM(IF(grp='window' AND m_fa = 0, m_days, 0)) AS window_days,
    COUNTIF(grp='early' AND m_fa = 0) AS n_lag,
    SUM(IF(grp='early' AND m_fa = 0, m_days, 0)) AS lag_days,
    COUNT(*) AS n_active_cur
  FROM j_cur
  GROUP BY 1
),
tok_prev AS (
  SELECT t.order_id, o.service_key,
    COALESCE(GREATEST(DATE_DIFF(LEAST(t.expires_at, p.pm_end), GREATEST(t.effective_at, p.pm_start), DAY) + 1, 0), 0) AS m_days
  FROM `shelikes-001.sheinc_intermediate.int_membership_tokens` t
  JOIN orders o USING (order_id)
  CROSS JOIN params p
  WHERE t.target_month = p.pm_start
    AND t.order_status = 1
    AND o.service_key IN ('money', 'multicreator')
),
fa_prev AS (
  SELECT f.order_id,
    SUM(f.monthly_accounting_without_tax + f.monthly_accounting_discount + f.monthly_accounting_own_expense) AS m_fa
  FROM `shelikes-001.sheinc_marts_accounting.monthly_accounting` f
  CROSS JOIN params p
  WHERE f.target_month = p.pm_start
  GROUP BY f.order_id
),
prev_agg AS (
  SELECT t.service_key,
    SAFE_DIVIDE(SUM(COALESCE(f.m_fa, 0)), NULLIF(SUM(t.m_days), 0)) AS fa_per_day_prev,
    COUNT(*) AS n_active_prev
  FROM tok_prev t LEFT JOIN fa_prev f USING (order_id)
  GROUP BY 1
)
SELECT
  service_key,
  c.fa_per_day_cur,
  p.fa_per_day_prev,
  IFNULL(c.n_window, 0) AS n_window,
  IFNULL(c.window_days, 0) AS window_days,
  IFNULL(c.n_lag, 0) AS n_lag,
  IFNULL(c.lag_days, 0) AS lag_days,
  IFNULL(c.n_active_cur, 0) AS n_active_cur,
  IFNULL(p.n_active_prev, 0) AS n_active_prev
FROM cur_agg c
FULL OUTER JOIN prev_agg p USING (service_key)
ORDER BY service_key
