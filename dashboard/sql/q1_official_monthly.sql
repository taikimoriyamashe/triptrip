-- q1_official_monthly: 公式月次FA(計上済み実績)。
-- ソース: monthly_financial_accounting(スプレッドシート公式モニタリングの元テーブル)。
-- 範囲: 当月(CURRENT_DATE('Asia/Tokyo')基準)の12ヶ月前 〜 テーブルにある未来月まで全部。
-- 出力列: year_month STRING 'YYYY-MM' / lks INT(likes財務会計) / mny INT(money財務会計) / pd INT(likespro財務会計)
SELECT
  year_month,
  `likes財務会計` AS lks,
  `money財務会計` AS mny,
  `likespro財務会計` AS pd
FROM `shelikes-001.sheinc_marts_output_spreadsheet_official_monitoring.monthly_financial_accounting`
WHERE year_month >= FORMAT_DATE('%Y-%m', DATE_SUB(DATE_TRUNC(CURRENT_DATE('Asia/Tokyo'), MONTH), INTERVAL 12 MONTH))
ORDER BY year_month
