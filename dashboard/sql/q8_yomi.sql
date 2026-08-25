-- q8_yomi: 社内売上ヨミ(オンライン・金額) [契約v1.2追加]。
-- ソース: likes_monthly_online_revenue_forecast(毎日更新の materialized 出力。
--   fy24 履歴のシナリオA・当日as_ofと完全一致することを確認済み。Drive外部テーブルは使用しない)。
-- yomi_total = レギュラー入会金ヨミ + レギュラー月額ヨミ + スタライ入会金ヨミ + スタライ月額ヨミ。
-- 注意: ヨミは財務会計(FA)と定義が異なる(T1実測: 2026-05〜07の月初時点ヨミはオンラインFA実績比 +2.6〜+3.8% 過大)。
--   表示上の扱いは targets.json の yomi.comparable フラグに従う(既定 false = 参考値表示)。
-- 範囲: 当月〜+2ヶ月の3行(CURRENT_DATE('Asia/Tokyo')基準)。
-- 出力列: month STRING 'YYYY-MM' / yomi_total FLOAT / as_of DATE(計算日)
WITH params AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH)) AS m0,
    FORMAT_DATE('%Y-%m', DATE_ADD(DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH), INTERVAL 2 MONTH)) AS m2
)
SELECT
  f.`対象月` AS month,
  f.`レギュラー入会金ヨミ` + f.`レギュラー月額ヨミ` + f.`スタライ入会金ヨミ` + f.`スタライ月額ヨミ` AS yomi_total,
  f.`計算日_as_of` AS as_of
FROM `shelikes-001.sheinc_marts_output_spreadsheet_official_monitoring.likes_monthly_online_revenue_forecast` f
CROSS JOIN params p
WHERE f.`対象月` BETWEEN p.m0 AND p.m2
ORDER BY month
