-- q1_official_monthly: 公式月次FA(計上済み実績)。
-- ソース: monthly_financial_accounting(スプレッドシート公式モニタリングの元テーブル)。
-- 範囲 [契約v1.3]: 前年度開始(=当年度開始の12ヶ月前) 〜 テーブルにある未来月まで全部。
--   SHEの年度は4月〜3月。当年度開始 = 基準日の月が4月以降なら当年4/1、3月以前なら前年4/1。
--   式は CURRENT_DATE('Asia/Tokyo') 基準で自己完結・年跨ぎ安全:
--   基準日を3ヶ月戻して年頭に丸め、3ヶ月進めると当年度4/1になる(例: 2026-08-25→2026-04-01 /
--   2027-03-15→2026-04-01 / 2027-04-01→2027-04-01)。その12ヶ月前が前年度4/1。
-- 用途: 通期達成シミュレーション(前年度通期実績の算出・当年度実績月の確定値)。
-- 出力列: year_month STRING 'YYYY-MM' / lks INT(likes財務会計) / mny INT(money財務会計) / pd INT(likespro財務会計)
SELECT
  year_month,
  `likes財務会計` AS lks,
  `money財務会計` AS mny,
  `likespro財務会計` AS pd
FROM `shelikes-001.sheinc_marts_output_spreadsheet_official_monitoring.monthly_financial_accounting`
WHERE year_month >= FORMAT_DATE(
  '%Y-%m',
  DATE_SUB(
    DATE_ADD(DATE_TRUNC(DATE_SUB(CURRENT_DATE('Asia/Tokyo'), INTERVAL 3 MONTH), YEAR), INTERVAL 3 MONTH),
    INTERVAL 12 MONTH
  )
)
ORDER BY year_month
