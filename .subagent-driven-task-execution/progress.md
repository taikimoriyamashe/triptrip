# 進捗台帳: プランナー制度設計向け実績データ再抽出（2026-09-09）

| Task | 状態 | 成果物 | 検証 | 担当/モデル |
| --- | --- | --- | --- | --- |
| T1 役割判定マスタ(as-of)と対象者検証 | complete | SQL内 metas CTE / 06_planner_roster_check.csv | BQで現在値vs as-of 分布を確認。planner_results の planner_term/grade は「現在値」であり実施日時点でないことを確認 | コントローラー(Fable) |
| T2 ①明細抽出 | fixing→re-extract | output/01_case_detail_2026.csv, scratchpad/raw_v2/*.json | v1抽出は行数一致(35,848)で完了。レビュー(opus)で C1 参加者重複二重計上 / C2 shift_key衝突 / C3 CMM単価 を検出 → SQL v2 (role_requirement_id をキーに追加, participant_row_rank 追加, 社員0円統一) で再抽出中 | 実行: sonnet(pinned) / レビュー: opus(pinned) |
| T3 ②③集計・派生指標 | fixing | scripts/aggregate.py | レビュー指摘 C1,I1〜I7,M1〜M8 を反映（主行のみ集計、リードシフト分母、分布は全個人、上位者閾値10、期間集計の社員判定、キャンセル率分母、役割粒度の統一）。v2サンプル(7/1-5)で動作確認 | コントローラー修正 → 全体レビューで再確認 |
| T4 報酬単価 | complete | README 報酬レートカード | Notion 業務規定(2026-07-16更新) | コントローラー |
| T5 定義書・納品 | pending | planner_analysis/README.md | 全体レビュー後にコミット | |
