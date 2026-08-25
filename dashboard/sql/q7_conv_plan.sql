-- q7_conv_plan: オンライン成約の社内計画(ヨミ)件数 [契約v1.2追加]。
-- ソース: likes_monthly_online_revenue_forecast_inputs(毎日更新の materialized 出力。
--   spread_sheet_v2 系の Drive 外部テーブルは権限不可のため使用しない)。
-- 範囲: 前月〜翌月の3行(CURRENT_DATE('Asia/Tokyo')基準)。
-- 意味: plan_regular + plan_sutara ≒ オンライン成約(valid)の月間計画。src_* = '実績'(確定月) | 'ヨミ'(計画)。
--   検証済み: 2026-07 実績行 637+172=809 ≒ q6/q5系の7月オンラインvalid実績806(±1%)。
--   拠点の計画はBQに存在しない(Drive外部のみ)→ targets.json / UI の手入力で扱う。
-- 出力列: month STRING 'YYYY-MM' / plan_regular FLOAT / plan_sutara FLOAT
--         / src_regular STRING / src_sutara STRING / as_of DATE(計算日)
WITH params AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_SUB(DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH), INTERVAL 1 MONTH)) AS pm,
    FORMAT_DATE('%Y-%m', DATE_ADD(DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH), INTERVAL 1 MONTH)) AS nm
)
SELECT
  f.`対象月` AS month,
  f.`成約数_レギュラー` AS plan_regular,
  f.`成約数_スタライ` AS plan_sutara,
  f.`成約数の出所_レギュラー` AS src_regular,
  f.`成約数の出所_スタライ` AS src_sutara,
  f.`計算日_as_of` AS as_of
FROM `shelikes-001.sheinc_marts_output_spreadsheet_official_monitoring.likes_monthly_online_revenue_forecast_inputs` f
CROSS JOIN params p
WHERE f.`対象月` BETWEEN p.pm AND p.nm
ORDER BY month
