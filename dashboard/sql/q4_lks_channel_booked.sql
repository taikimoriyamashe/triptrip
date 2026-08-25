-- q4_lks_channel_booked: LKS 計上済みFA(当月)のチャネル按分。
-- entrance = likes_conversions(is_valid_conversions IS TRUE)の user_id ごと最初(purchased_at昇順 rn=1)の trial_lesson_type。
-- channel: trial_lesson_type LIKE '拠点%' → '拠点' / IN ('OTL','ONS') → 'オンライン' / それ以外(NULL含む) → '分類不能'。
--          当月FAがあるのに有効成約記録が無い user → '成約記録なし'。
-- FA: int_likes_financial_accounting の user_id 別 当月SUM(without_tax + discount + own_expense)。
-- 検証: Σ booked_fa = q1 の当月 lks と一致すること(スレッド実績: 完全一致)。
-- 出力列: channel STRING / n_users INT / booked_fa INT
WITH params AS (
  SELECT DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH) AS m_start
),
entrance AS (
  SELECT user_id, trial_lesson_type,
    ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY purchased_at ASC) AS rn
  FROM `shelikes-001.sheinc_marts_fy24.likes_conversions`
  WHERE is_valid_conversions IS TRUE
),
user_ch AS (
  SELECT user_id,
    CASE
      WHEN trial_lesson_type LIKE '拠点%' THEN '拠点'
      WHEN trial_lesson_type IN ('OTL', 'ONS') THEN 'オンライン'
      ELSE '分類不能'
    END AS channel
  FROM entrance
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
  COALESCE(uc.channel, '成約記録なし') AS channel,
  COUNT(*) AS n_users,
  SUM(fa.m_fa) AS booked_fa
FROM fa
LEFT JOIN user_ch uc USING (user_id)
GROUP BY 1
ORDER BY booked_fa DESC
