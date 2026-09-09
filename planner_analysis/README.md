# プランナー制度設計向け 実績データ再抽出（SE／EP／CP／その他）

- 抽出日時: 2026-09-09 04:30〜 UTC（13:30〜 JST）
- 依頼: プランナー制度設計・SEとの比較・CAC/ROI検証のための個人×月×役割区分の実績データ（①案件明細、②個人×月、③役割別月次）
- データソース: BigQuery `shelikes-001`（dbt marts）。Lightdash プロジェクト `SHE_planner` / `SHE` の同名 Explore と同じテーブルを参照。
- 成果物（`planner_analysis/output/`）
  - `planner_productivity_2026.xlsx` … 全シート入り（明細は2026-07以降のみ。全期間明細はCSV）
  - `01_case_detail_2026.csv.gz` … ①案件単位明細（2026-01-01〜2026-09-30 登録分、35,848行×122列。gzip圧縮。`gunzip -k` で展開）
  - `02_individual_month.csv` / `02b_individual_period.csv` … ②個人×月×役割区分 / 個人×期間（2026-07以降、2026-01以降）
  - `03_role_month.csv` / `03b_role_subcategory_month.csv` / `03c_role_period.csv` … ③役割別月次 / サブ区分別 / 期間合計
  - `04_top_performers_period.csv` / `04b_top_performers_month.csv` … 成約率とシフト数の両方が役割中央値以上の上位者
  - `05_reconciliation.csv` … 既存集計「その他」参考値との検算
  - `06_planner_roster_check.csv` … SE15名リスト照合・表記揺れ・SE判定
  - `07_role_change_history.csv` … 対象期間中に役割区分が変わった人の履歴
- 再現手順: `sql/01_case_detail.sql` の `@start_date/@end_date` を置換して BigQuery で実行 → `scripts/bq_result_to_csv.py` でCSV化 → `python3 scripts/aggregate.py output/01_case_detail_2026.csv output 2026-09-09`

## 1. 最新確定日・データ更新日時（2026-09-09 13:00 JST 時点）

| テーブル | 最終更新 (UTC) | 備考 |
| --- | --- | --- |
| `sheinc_marts_fy24.likes_trial_lesson_planner_results` | 2026-09-09 03:52 | 行 148,403。starts_at 最大 2026-09-30（未来の登録シフト含む）。purchased_at 最大 2026-09-09 12:07 JST |
| `sheinc_marts_fy24.likes_trial_lesson_enhanced` | 2026-09-09 04:01 | 行 385,439 |
| `sheinc_marts_fy24.lightdash_likes_coolingoff` | 2026-09-09 02:27 | |
| `sheinc_intermediate.int_planner_metas` (VIEW) | ソース `stg_shelikes_sheprodb__planner_metas`（Fivetran） | 役割履歴 |
| `sheinc_marts_fy24.likes_conversions` / `likes_member_terms` | dbt日次 | 売上・受講開始/退会 |

- **最新確定日（実施ベース）**: 2026-09-09（当日分は途中）。**実施日 2026-09-08 までが出欠確定**。
- **成約の確定**: クーリングオフは契約書面到着日から8日（`cooling_off_deadline_date`）。**2026-08-25 頃以降の成約はまだクーオフが発生し得る**ため「最終有効成約」は暫定値。9月分は特に暫定。
- 過去分でも出欠未反映（`attendance_status=1: シフト登録中`）が 902 行（2026-01以降）ある。これらは「登録のみ・実施未確定」として実施数から除外。

## 2. 役割区分（SE／EP／CP／その他）の判定方法

**重要**: `likes_trial_lesson_planner_results.planner_term / planner_grade` は **現在値**（最新の planner_metas）で、実施日時点ではない（例: 大場史子は 2026-08-25 に SE→career に変更されたが、2026年の全行が career 表示）。本抽出では `sheinc_intermediate.int_planner_metas` の `effective_at` 履歴を使い **実施日時点（as-of）** で判定した。

| 区分 | 判定 (as-of の generation / career_path) | 備考 |
| --- | --- | --- |
| SE | `generation IN ('SE','SE_onboarding')` | 社員（正社員シフト）。SE_onboarding は研修中SE。サブ区分で区別 |
| EP | `career_path = 'expert'` | Expert Planner（業務委託） |
| CP | `career_path = 'career'` | Career Planner（業務委託）。generation が「N期」の契約プランナーおよび generation「その他」の新規CP（例: 安高きら, 皆葉薫 等）を含む |
| その他 | 上記以外 | サブ区分: `その他_generalist(社員)`（事推社員プランナー: 山口沙恵理, 和田梓 等 2026-06-03 に other→generalist へ一斉変更）、`その他_CMM(拠点)`（拠点コミュニティマネージャー）、`その他_社員等(メタなし/未設定)`（planner_metas がない社員: 8月末の全社員シフト募集の一時参加者等）、`その他_other`（2026-06-03 以前の generalist/CMM 相当。career_path='other' かつ generation='その他' の社員: 山口沙恵理・和田梓・吉野いくみ 等）、`その他_legacy_*` |

- 判定の優先順位: ①実施日時点の履歴（`effective_at <= starts_at < 次のeffective_at`）→ ②履歴がその日より後にしか無い場合は最初の履歴で後方補完（`role_source='backfilled_first_meta'`。例: 佐藤智子 初回メタ 2026-01-13 だが初シフト 01-12）→ ③メタなしは planner_results の現在値（`role_source='current_from_results'`）。
- 兼務: 1人は同時点で1区分のみ。月の途中で区分が変わった場合、②個人×月では **区分ごとに別行**（`role_changed_in_month=TRUE`, `n_roles_in_month`）。`07_role_change_history.csv` に変更日（`asof_effective_at`）を記載。
- 2026-07-01 以降に区分変更があった主な例: 大場史子（SE→CP 2026-08-25）、松坂瑠依・白井悠里名・佐々木静香（CP→EP 2026-08-31）、坂元有沙（career⇄expert を複数回）。

## 3. シフト・参加者・成約の定義

| 指標 | 定義（`likes_trial_lesson_planner_results` の列） |
| --- | --- |
| シフト単位 | `shift_key = trial_lesson_id-planner_id-trial_lesson_role_requirement_id-position_no`（1体験レッスンの1ロール要件×1ポジション）。同一レッスンで2ポジション（例: プランナー＋ファシ）を持つ場合は **2シフト・2件分の報酬**として計上（2026年で約2,700 lesson×planner ペア） |
| シフト登録数 `shift_registered` | assign_status=approved の全シフト（未来日含む。`shift_registered_future` で内訳） |
| 確定シフト数 `shift_confirmed` | 登録のうちご自愛（シフトカット, attendance=4）されていないもの |
| 実施シフト数 `shift_attended` | `attendance_status=2 (出席)` |
| 最低保証 `shift_min_guarantee` | `attendance_status=5`（開催なし。業務委託は1,500円） |
| シフトキャンセル数 | `shift_jiai_cancel`（ご自愛=シフトカット, 4）＋`shift_absent`（欠席, 3）。**プランナー本人の事前キャンセル（assign削除）はソースに残らないため取得不可**（`assign_cancelled_at` は全行NULL）。`cancel_rate_shift` = (ご自愛+欠席)/実施日到来済み登録数 `shift_registered_past` |
| 参加者数 `participants_lead / participants_all` | プランナーに紐づく `trial_lesson_reservation_id` のユニーク数。**同一プランナー×同一予約に役割違いの行（プランナー行＋ファシ/リーダー/サポプラ行）が複数存在**するため、`participant_row_rank=1`（プランナー>サポプラ>ファシ>リーダー の優先）の主行のみで成果・参加者を集計する（2026年で2,851行を除外。除外しないと成約数が約13%過大）。紐づく予約は実質全て参加済み（53行のみ enhanced 側 is_attended=false）。`_lead` は 1on1/1on2/1on3 と GCリードのみ、`_all` は GCアシスト・Lounge・離脱を含む |
| 1on1リード数 / GCリード数 / GCアシスト数 | 主行のうち `counselings_style IN ('1on1','1on2','1on3')` / `counselings_style='GC' AND gc_style='lead'` / `gc_style='assistant'` の参加者数（1on2/1on3 は1on1リードに含む） |
| その場成約 | `conversion_status IN ('ご入会','クーリングオフ') AND is_the_day_cv=TRUE`（レッスン当日成約、クーオフ前） |
| 追客成約（後日成約） | 同上で `is_the_day_cv=FALSE`（レッスン後の入会。体験レッスンに紐づいた注文のみ） |
| 合計成約（クーオフ前） `gross_lead` | ご入会＋クーリングオフ |
| クーリングオフ `coolingoff_lead` | `conversion_status='クーリングオフ'`（`refunded_at` = クーオフ日）。`coolingoff_rate_lead` = クーオフ/合計成約 |
| 最終有効成約 `valid_lead` | `conversion_status='ご入会'`（＝Lightdash「最終レッスン成約数（クーオフ除く）」`countd_valid_conversion_uu` と同定義）。お試し入会（`is_trial_membership`）は本入会移行後にご入会となる |
| 成約率 | 分子=リード成約数、分母=リード参加者数（`participants_lead`）。`same_day_rate_lead`（その場成約率）、`gross_rate_lead`（クーオフ前）、`valid_rate_lead`（最終有効）はいずれも分母が同じ。`valid_rate_all` は GCアシスト等も分母・分子に含めた参考値（既存集計に近い） |
| VCV | `likes_trial_lesson_enhanced.virtual_cv`（参加者属性から予測した期待成約数）。`weighted_vcv_achievement` = 加重成約 / 加重VCV（1on1=1.0, GCリード=0.7, GCアシスト=0.3。`likes_trial_lesson_planner_vcv_monitoring` と同じ重み） |
| 成約難易度 | `incentive_status` から抽出（難易度1〜6 = `virtual_cv_rank`）。2025-08以降は参加時意向度ではなく難易度でインセンティブが決まる |

### GCアシストの計上
- リード（1on1／GCリード）の成果は **リードプランナー**に計上（`*_lead`）。GCアシストの成約は `gross_assist / valid_assist` として別列に集計し、主要成約率には含めない。
- 参考として加重（0.7/0.3）方式 `weighted_valid` と、全行ベース `valid_all` も併記。
- 成約インセンティブはBQの `conversion_incentive` に従い、アシストは半額で計上済み。

### キャンセル・担当変更・重複・空欄の処理
- レッスン自体のキャンセル（開催なし）は最低保証（attendance=5）として残る。参加者の予約キャンセルは planner_results に行が残らない。
- 担当変更: 参加者行は planner_results の紐付け（カウンセリング担当）に従う。`planner_relation_in_enhanced` で enhanced 側のリード/アシストと照合できる（`other` は不一致）。
- 重複: 同一プランナー×同一予約の役割違い行は主行1行に集約（上記）。同一氏名の複数ID（鎌田莉佳: 162308 と 408913）は `⑥b同名複数ID` に記載。
- 担当者空欄: planner_results は planner_id 必須のため空欄なし。`role_source='current_from_results'` で planner_metas がない社員 47名（2026年）は「その他_社員等」に分類。
- 再注文キャンセル（3行）は成約・クーオフのどちらにも数えない。

## 4. 報酬の算出方法

業務委託プランナーのレートカード（Notion「業務規定」2026-07-16更新、事業推進ユニット）:

| 項目 | 単価 | 本抽出での計上 |
| --- | --- | --- |
| CP シフトイン（プランナー役割・出席） | 2,500円/回 | `shift_fee_planner_base_yen` |
| EP シフトイン | 4,000円/回 | 同上 |
| ファシリテーター / サポートプランナー | 1,500円/回 | `shift_fee_leader_facil_support_yen` |
| リーダー | 10,000円/回 | 同上 |
| 最低保証（開催なし） | 1,500円（役割問わず） | `shift_fee_min_guarantee_yen` |
| 成約インセンティブ（難易度1〜6） | 500 / 1,000 / 3,000 / 3,700 / 4,500 / 5,000円（GCアシストは半額） | `incentive_yen_payable`（BQ `conversion_incentive`。BQ側で既に「ご入会かつ支払対象」の行にのみ値が入っている。クーオフ分の相当額は取得不可） |
| 拠点スポットプランナー加算（ファシ1,500・シフキャン1,000等） | 拠点により異なる | **未計上**（OTL単価で計上。拠点行は `lesson_type LIKE '拠点_%'` で識別可） |
| プレプランナー研修費 5,000円、交通費 | — | 未計上 |

- **SE・generalist・CMM・旧other・社員等は給与制**のためシフト報酬・インセンティブは 0 円で計上（`is_employee=TRUE`）。SE の給与・人件費は BigQuery に存在しないため、年収1,000万円の採算性検証には別途人事データが必要。参考として、SEが業務委託レートだった場合のインセンティブ相当額を `incentive_yen_payable` に残している。
- `total_reward_yen` = シフト報酬 `shift_fee_yen` + 支払対象インセンティブ `incentive_yen_paid`（業務委託のみ。社員は0）。実施月ベース（実際の支払はシフト報酬=翌月末、インセンティブ=翌々月末）。
- 1シフト当たり成約: `valid_per_lead_shift` = リード最終有効成約 ÷ **リード参加者を1人以上担当した実施シフト数** `shift_attended_with_lead`（リーダー/ファシ/サポプラのみのシフトを分母から除く）。`valid_per_shift_all` は全実施シフトを分母にした参考値。
- 分布（③の `*_min/p25/median/p75/max`）は **リード参加者1人以上の個人**全員で算出。`headcount_sample_ge30` で月間リード参加者30人以上の人数を併記。②個人×月の `sample_insufficient_flag` は月間リード参加者30人未満。
- 上位者（④）: リード参加者10人以上の個人を対象に、役割内で `valid_rate_lead` と `shift_attended_with_lead` が**ともに中央値以上**の人を `is_top_both=TRUE`。順位列も併記。

## 5. 収益・LTV

- `entrance_fee_valid_lead`: 最終有効成約の入会金（税抜, `entranceamount_without_tax` = `likes_conversions.sales_without_tax`）
- `plan_amount_valid_lead`: 入会プラン金額（`membership_plan_amount`, 例: レギュラー12ヶ月 322,000円）。**初月売上概算** `first_month_revenue_valid_lead` = 入会金 + プラン金額
- LTV: `likes_member_terms` の `likes_begin_date / likes_withdrawal_date` から `days_enrolled_so_far` を明細に付与（2026-07以降の成約は受講開始直後のため LTV 実績は未成熟）。粗利は原価データがBQに無いため未算出。

## 6. 参照テーブル・Lightdash

| 用途 | BigQuery | Lightdash Explore (SHE_planner: `b3202e91-93ed-4916-a2b8-28afef3ce902`) |
| --- | --- | --- |
| プランナー×レッスン×参加者（主表） | `shelikes-001.sheinc_marts_fy24.likes_trial_lesson_planner_results` | https://app.lightdash.cloud/projects/b3202e91-93ed-4916-a2b8-28afef3ce902/tables/likes_trial_lesson_planner_results |
| 参加者属性・VCV・収益・クーオフ | `shelikes-001.sheinc_marts_fy24.likes_trial_lesson_enhanced` | https://app.lightdash.cloud/projects/b3202e91-93ed-4916-a2b8-28afef3ce902/tables/likes_trial_lesson_enhanced |
| 月別プランナー報酬サマリ（件数のみ） | `shelikes-001.sheinc_marts_fy24.likes_monthly_planner_cost_summaries` | https://app.lightdash.cloud/projects/b3202e91-93ed-4916-a2b8-28afef3ce902/tables/likes_monthly_planner_cost_summaries |
| VCV達成率モニタリング | `shelikes-001.sheinc_marts_fy24.likes_trial_lesson_planner_vcv_monitoring` | https://app.lightdash.cloud/projects/b3202e91-93ed-4916-a2b8-28afef3ce902/tables/likes_trial_lesson_planner_vcv_monitoring |
| クーリングオフ | `shelikes-001.sheinc_marts_fy24.lightdash_likes_coolingoff` | https://app.lightdash.cloud/projects/b3202e91-93ed-4916-a2b8-28afef3ce902/tables/lightdash_likes_coolingoff |
| 役割履歴 | `shelikes-001.sheinc_intermediate.int_planner_metas` | https://app.lightdash.cloud/projects/b3202e91-93ed-4916-a2b8-28afef3ce902/tables/int_planner_metas |
| 売上・受講期間 | `shelikes-001.sheinc_marts_fy24.likes_conversions`, `likes_member_terms` | — |

参考にした保存チャート（SHE プロジェクト）:
- 「⛄️SE」 https://app.lightdash.cloud/projects/f6d1e4ea-827b-47fd-ab6e-c803a99df759/saved/b03636c6-0e4c-41cb-9bbd-f3eb5f507050/view （SE7名の明細。likes_trial_lesson_enhanced ベース）
- 「プランナー種別レッスン対応比率」 https://app.lightdash.cloud/projects/f6d1e4ea-827b-47fd-ab6e-c803a99df759/saved/a4851ae3-8b98-47b5-a6c8-3fbd9652f041/view （lead_planner_grade 別）
- 「プランナーの指定枠シフトイン率（SE/SE_onboarding）」 https://app.lightdash.cloud/projects/f6d1e4ea-827b-47fd-ab6e-c803a99df759/saved/3ad8f3b1-b28e-44c3-b44c-4ec08cd9e930/view
- 本抽出中に実行した Lightdash クエリ（共有リンク）: https://app.lightdash.cloud/share/XQHuqSxD0zsISS3YkIyMD （planner_term×grade×role 分布）, https://app.lightdash.cloud/share/1kKjrXLfyeXkooGb4oJ4C （lead_planner_grade 別月次）

## 7. 検算（既存集計「その他」との比較）

| 月 | 既存集計参考値（依頼記載） | A: 現在値 planner_term=SE & role=プランナー（主行のみ） | B: 実施日時点 SE（全役割・GCアシスト含む） | C: 実施日時点 SE & リードのみ（本表の成約率定義） |
| --- | --- | --- | --- | --- |
| 2026-07 | 参加 793 / 最終成約 310 / 39.09% | 789 / 316 / 40.05% | 841 / 332 / 39.48% | 798 / 314 / 39.35% |
| 2026-08 | 669 / 264 / 39.46% | 682 / 266 / 39.00% | 713 / 278 / 38.99% | **670 / 264 / 39.40%** |
| 2026-09途中 | 191 / 59 / 30.89% | 212 / 65 / 30.66% | 212 / 65 / 30.66% | 195 / 61 / 31.28% |

- 既存集計の「その他」は **SE社員14〜15名の成績**（`planner_grade='other'` = SE のグレード値）であり、社員一般ではない。本抽出の C（SE・リード）と8月は最終成約数が完全一致、7月も参加数±5・成約数±4で整合。
- 差分理由: ①データ取得日の差（9月は本抽出が9/9まで含む。7月の成約は後日成約・お試し→本入会の反映で増加、クーオフ確定で減少）②役割判定の差（既存は現在値のため、8/25にCPへ変更された大場史子の7〜8月分がSEから外れる。本抽出は実施日時点でSEに含める）③集計行の差（既存集計は GCアシスト行や複数役割行を含む可能性。本抽出は主行のみ・リードのみ）④成約定義は同一（`conversion_status='ご入会'`＝Lightdash「最終レッスン成約数（クーオフ除く）」）。

## 8. SE対象者リストの照合（`06_planner_roster_check.csv`）

- リスト15名は全員 `int_planner_metas` に generation SE（または SE_onboarding）で存在し、2026年に実施シフトあり（1人あたり 394〜469 実施シフト）。表記揺れなし。
- **大場史子**（planner_id 430979）: 2026-08-25 付で SE → career_path=career（CP, generation その他）に変更。実施日時点判定では 2026-08-16 まで SE、9月分は CP として集計（既存集計「その他」からは7〜8月分も外れている）。
- **SE_onboarding**（研修中SE）: 安達美里・吉野由佳・平原奈津子・中村江里・木村駿（2025-12-12〜2026-01-31頃）、五師光葉（2026-01-30〜02-28）。本抽出では SE に含めサブ区分 `SE(SE_onboarding)` で識別可。
- リスト外で SE 判定になった人: なし。
- 担当者名空欄: なし（planner_id 必須）。氏名に空白を含む表記揺れ候補: 松浦　里紗、西野 響子、齋藤 文（いずれも SE ではない）。
- 同一氏名で複数ID: 鎌田莉佳（162308 / 408913、社員含むその他）。
- 退職・休職: `asof_planner_status`（operating / graduated / task_suspension）を明細に付与。2026-07以降の SE は全員 operating。
- 8月末に区分変更があった業務委託: 松坂瑠依・白井悠里名・佐々木静香（CP→EP, 2026-08-31）、高橋亮佑・金森有美（EP→CP, 2026-08-31）、太田百香（EP→CP, 2026-08-04）。`07_role_change_history.csv` 参照。

## 9. 主要結果（2026-07-01〜2026-09-09、リード成約ベース。`03c_role_period.csv`）

| 区分 | 人数(稼働) | 実施シフト | リード担当シフト | リード参加者 | その場成約(クーオフ前) | 最終有効成約 | 最終成約率 | クーオフ率 | 1リードシフト当たり最終成約 | 総報酬(業務委託) | 報酬/最終成約 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SE | 15 | 1,796 | 1,665 | 1,668 | 648 | 639 | 38.3% | 13.8% | 0.384 | （給与・未取得。業務委託換算インセンティブ 2,647,100円） | — |
| EP | 17 | 1,750 | 1,252 | 1,254 | 515 | 471 | 37.6% | 16.6% | 0.376 | 8,097,800円 | 17,193円 |
| CP | 107 | 3,863 | 2,668 | 2,681 | 813 | 753 | 28.1% | 17.3% | 0.282 | 15,863,200円 | 21,067円 |
| その他 | 57 | 825 | 642 | 741 | 263 | 233 | 31.4% | 22.3% | 0.363 | （社員・0円） | — |

- 2026-01以降の通期（`period='2026-01以降'`）も同ファイルに収録。
- 9月分はクーオフ未確定・途中月のため暫定。

## 10. 既知の制約・注意
- planner_results の `planner_term/grade` は現在値。Lightdash 既存チャートで「SE」「other」を絞り込む集計は、役割変更者（大場史子等）の過去分を現在の区分で数えている。
- 業務委託の報酬はレートカードからの推計であり、freee業務委託管理の請求実績とは一致しない可能性がある（拠点加算、研修費、特例）。
- SE の人件費は未取得。
- 9月は途中月、かつクーオフ未確定。
