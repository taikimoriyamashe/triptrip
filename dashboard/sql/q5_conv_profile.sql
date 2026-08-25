-- q5_conv_profile: 成約日別・1件あたり当月FA プロファイル(前月実測)。
-- 前月に最初の有効成約をした user(likes_conversions rn=1 が前月)について、
-- 成約日(日 dom)× channel ごとに件数と「その userたちの前月FA合計 ÷ 件数」を出す。
-- 用途: ③今後の新規成約分 = pace × Σ profile(残日)、②既成約の未計上残 = Σ max(0, n×profile − booked)。
-- 注意: 拠点の per-dom 単価はノイズが大きい → 利用側はオンラインプロファイル×スケール係数を使う(契約参照)。
-- 出力列: channel STRING / dom INT(1..31) / n_conv INT / fa_per_conv FLOAT
WITH params AS (
  SELECT
    DATE_SUB(DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH), INTERVAL 1 MONTH) AS pm_start,
    DATE_SUB(DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH), INTERVAL 1 DAY) AS pm_end
),
entrance AS (
  SELECT user_id, DATE(purchased_at) AS pdate, trial_lesson_type,
    ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY purchased_at ASC) AS rn
  FROM `shelikes-001.sheinc_marts_fy24.likes_conversions`
  WHERE is_valid_conversions IS TRUE
),
prev_conv AS (
  SELECT e.user_id,
    EXTRACT(DAY FROM e.pdate) AS dom,
    CASE
      WHEN e.trial_lesson_type LIKE '拠点%' THEN '拠点'
      WHEN e.trial_lesson_type IN ('OTL', 'ONS') THEN 'オンライン'
      ELSE '分類不能'
    END AS channel
  FROM entrance e
  CROSS JOIN params p
  WHERE e.rn = 1 AND e.pdate BETWEEN p.pm_start AND p.pm_end
),
fa AS (
  SELECT f.user_id,
    SUM(f.monthly_accounting_without_tax + f.monthly_accounting_discount + f.monthly_accounting_own_expense) AS m_fa
  FROM `shelikes-001.sheinc_intermediate.int_likes_financial_accounting` f
  CROSS JOIN params p
  WHERE f.target_month = p.pm_start
  GROUP BY f.user_id
)
SELECT
  c.channel,
  c.dom,
  COUNT(*) AS n_conv,
  SAFE_DIVIDE(SUM(COALESCE(fa.m_fa, 0)), COUNT(*)) AS fa_per_conv
FROM prev_conv c
LEFT JOIN fa USING (user_id)
GROUP BY 1, 2
ORDER BY 1, 2
