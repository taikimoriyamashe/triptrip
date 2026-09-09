# 進捗台帳: プランナー制度設計向け実績データ再抽出（2026-09-09）

| Task | 状態 | 成果物 | 検証 | 担当/モデル |
| --- | --- | --- | --- | --- |
| T1 役割判定マスタ(as-of)と対象者検証 | complete | SQL内 metas CTE / 06_planner_roster_check.csv | BQで現在値vs as-of 分布を確認。planner_results の planner_term/grade は「現在値」であり実施日時点でないことを確認 | コントローラー(Fable) |
| T2 ①明細抽出 | in_progress→review | output/01_case_detail_2026.csv, scratchpad/raw/*.json | チャンク行数 = BQ COUNT(*) | 実行: sonnet(pinned) |
| T3 ②③集計・派生指標 | in_progress | scripts/aggregate.py, output/*.csv, planner_productivity_2026.xlsx | 7/1-15チャンクでテスト済 | コントローラー作成 → 独立レビュー予定 |
| T4 報酬単価 | complete | README 報酬レートカード | Notion 業務規定(2026-07-16更新) | コントローラー |
| T5 定義書・納品 | pending | planner_analysis/README.md | 全体レビュー後にコミット | |
