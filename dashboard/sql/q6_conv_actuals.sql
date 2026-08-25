-- q6_conv_actuals: 当月の成約実績(channel×日)。
-- 当月に purchased_at がある成約(有効/無効両方)を user ごとに purchased_at 昇順で並べ、rn=1 の成約を数える。
-- n_valid(is_valid_conversions 基準)が事業側認識の成約件数と一致する(スレッド実証: オンライン498 vs 認識500)。
-- booked_fa_valid = 有効成約 user の当月FA合計(int_likes_financial_accounting の user_id 別SUM)。
-- 出力列: channel STRING / dom INT / n_all INT / n_valid INT / booked_fa_valid INT
WITH params AS (
  SELECT
    DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH) AS m_start,
    LAST_DAY(CURRENT_DATE('Asia/Tokyo'), MONTH) AS m_end
),
ent AS (
  SELECT c.user_id, DATE(c.purchased_at) AS pdate, c.trial_lesson_type, c.is_valid_conversions,
    ROW_NUMBER() OVER (PARTITION BY c.user_id ORDER BY c.purchased_at ASC) AS rn
  FROM `shelikes-001.sheinc_marts_fy24.likes_conversions` c
  CROSS JOIN params p
  WHERE DATE(c.purchased_at) BETWEEN p.m_start AND p.m_end
),
conv AS (
  SELECT user_id, pdate, is_valid_conversions,
    EXTRACT(DAY FROM pdate) AS dom,
    CASE
      WHEN trial_lesson_type LIKE '拠点%' THEN '拠点'
      WHEN trial_lesson_type IN ('OTL', 'ONS') THEN 'オンライン'
      ELSE '分類不能'
    END AS channel
  FROM ent
  WHERE rn = 1
),
fa AS (
  SELECT f.user_id,
    SUM(f.monthly_accounting_without_tax + f.monthly_accounting_discount + f.monthly_accounting_own_expense) AS m_fa
  FROM `shelikes-001.sheinc_intermediate.int_likes_financial_accounting` f
  CROSS JOIN params p
  WHERE f.target_month = p.m_start
  GROUP BY f.user_id
)
SELECT
  c.channel,
  c.dom,
  COUNT(*) AS n_all,
  COUNTIF(c.is_valid_conversions) AS n_valid,
  SUM(IF(c.is_valid_conversions, COALESCE(fa.m_fa, 0), 0)) AS booked_fa_valid
FROM conv c
LEFT JOIN fa USING (user_id)
GROUP BY 1, 2
ORDER BY 1, 2
