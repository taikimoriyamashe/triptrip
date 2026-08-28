> 注: 本文書はダッシュボード構築時の確定仕様(データ契約)の写しである。原本はセッション作業領域で管理されていたため、参照用にリポジトリへ収載した。文中のscratchpadパスは構築時の作業環境のもの。

# 財務売上着地モニター データ契約 v1.3

依頼元スレッド: Slack「7月財務売上」(C0AMVPZEN5N / 1784613152.986259)。全文: scratchpad/thread_messages.txt
目的: LKS(SHElikes)・MNY(SHEmoney)・プロデ(SHElikes PRO=multicreator)の「今月これから計上予定の財務売上(FA)」と「月末着地見込み」を、Devinへの都度依頼なしで自動確認できるダッシュボードにする。

## 全体アーキテクチャ

- BigQuery プロジェクト `shelikes-001` に対する 6 本の読み取り専用クエリ(`dashboard/sql/q1..q6*.sql`)。
- すべて `CURRENT_DATE('Asia/Tokyo')` 基準で自己完結(パラメータ不要・いつ実行しても当月を対象化)。BigQuery scripting(DECLARE)は使用不可 — 単一SELECT文のみ(WITH可)。
- スナップショット `dashboard/data/latest.json` = クエリ結果を型付きで格納。`dashboard/build.py` が `dashboard/template.html` の `/*__DATA__*/` プレースホルダに注入 → `dashboard/out/dashboard.html`。
- ページは Claude Artifact として公開。`mcp` capability により、閲覧者の「Google Cloud BigQuery」コネクタで同じSQLをライブ再実行可能(コネクタが無い閲覧者は埋め込みスナップショットを閲覧)。
- 計算ロジックはすべてクライアントJS(`dashboard/logic.js`、純関数・DOM非依存)に集約。スナップショット経路とライブ経路が同じ関数を通る。

## クエリ契約(列名・型は厳守。SQLの列エイリアスがそのままJSONキーになる)

### q1_official_monthly — 公式月次FA(計上済み実績)
ソース: `shelikes-001.sheinc_marts_output_spreadsheet_official_monitoring.monthly_financial_accounting`
- 範囲: 当月の12ヶ月前 〜 テーブルにある未来月まで全部。
- 列: `year_month` STRING 'YYYY-MM' / `lks` INT(=`likes財務会計`) / `mny` INT(=`money財務会計`) / `pd` INT(=`likespro財務会計`)
- 検証済み: 2026-07 実績 lks=356,607,091 / mny=4,619,550 / pd=9,080,741。2026-08 は 8/25時点で lks=351,200,806 / mny=4,492,633 / pd=10,160,505。

### q2_lks_pending — LKS 既存会員の課金更新残・滞納/処理ラグ(プラン×支払種別)
ソース: `sheinc_intermediate.int_membership_tokens` t JOIN `sheinc_marts_accounting.all_orders` o USING(order_id)、FA: `sheinc_intermediate.int_likes_financial_accounting`(order_id別 当月SUM: monthly_accounting_without_tax + monthly_accounting_discount + monthly_accounting_own_expense)
- 対象: t.target_month = 当月初日, t.order_status = 1, t.plan_name IN ('レギュラープラン','スタンダードプラン','ライトプラン','卒業生プラン')
- 当月内有効日数 m_days = GREATEST(DATE_DIFF(LEAST(expires_at, 月末), GREATEST(effective_at, 月初), DAY)+1, 0)
- グループ: effective_at < 基準日 → 'early' / それ以外 → 'window'
- 単価: fa_per_day_cur = SAFE_DIVIDE(Σ early FA, Σ early m_days)。**fa_per_day_prev** = 前月(target_month=前月初日)の同計算(前月は全日earlyなので全体)。月初で early が空のときのフォールバック用。
- **新規会員除外(①④と②③の二重計上防止)** [v1.1改定・R1裁定]: 当月に最初の有効成約をした user の order を **window(①)と lag(④)の両方**の集計から除外する(①④=既存会員のみ / ②③=当月新規のみ、という対称な切り分け)。q3(MNY/プロデ)は②③が存在しないため除外しない。
- **防御的堅牢化** [v1.1]: all_orders はJOIN前に order_id で1行化(重複1,258件の将来ファンアウト防止)。m_days は COALESCE(GREATEST(...,0),0) で NULL を 0 に正規化(COUNTIF/SUM の非対称防止)。
- 出力列(プラン×支払種別ごと1行): `plan_name` STRING / `payment_type` STRING / `fa_per_day_cur` FLOAT(NULL可) / `fa_per_day_prev` FLOAT(NULL可) / `n_window` INT / `window_days` INT / `n_lag` INT(early かつ FA=0 の件数=滞納/処理ラグ) / `lag_days` INT

### q3_mny_pd_pending — MNY・プロデ同等集計(service別)
ソース: 同上だが o.service_key IN ('money','multicreator')、FAは `sheinc_marts_accounting.monthly_accounting`(order_id別SUM、同3列)
- 出力列(service_keyごと1行): `service_key` / `fa_per_day_cur` / `fa_per_day_prev` / `n_window` / `window_days` / `n_lag` / `lag_days` / `n_active_cur` INT(当月トークン件数) / `n_active_prev` INT(前月トークン件数)
- 既知の仕様: multicreator は marts monthly_accounting にFAが載らない別パイプライン → fa_per_day は NULL/0 になる(=正しい挙動)。件数列だけ意味を持つ。

### q4_lks_channel_booked — LKS 計上済みFAのチャネル按分
- entrance = likes_conversions(`sheinc_marts_fy24.likes_conversions`, is_valid_conversions IS TRUE)の user_id ごと最初(purchased_at昇順 rn=1)の trial_lesson_type。
- channel: trial_lesson_type LIKE '拠点%' → '拠点' / IN ('OTL','ONS') → 'オンライン' / それ以外(NULL含む) → '分類不能'。成約記録が無い user → '成約記録なし'。
- FA: int_likes_financial_accounting を user_id で当月SUM。
- 出力列: `channel` STRING / `n_users` INT / `booked_fa` INT
- 検証: Σ booked_fa ≒ q1 当月 lks(スレッド実績: 99.9%以上一致)。

### q5_conv_profile — 成約日別・1件あたり当月FA プロファイル(前月実測)
- 前月に最初の有効成約をした user について、成約日(日 dom)× channel ごとに: 件数と、その user たちの前月FA合計÷件数。
- 出力列: `channel` STRING / `dom` INT(1..31) / `n_conv` INT / `fa_per_conv` FLOAT
- 用途: ③今後の新規成約分 = pace × Σ profile(残日)、②既成約の未計上残 = Σ max(0, n×profile − booked)。

### q6_conv_actuals — 当月の成約実績(channel×日)
- 当月に purchased_at がある user の rn=1 成約(有効/無効両方数える)。
- 出力列: `channel` STRING / `dom` INT / `n_all` INT / `n_valid` INT(is_valid_conversions) / `booked_fa_valid` INT(有効成約userの当月FA合計)
- 検証: 事業側認識の成約件数は n_valid 基準と一致する(スレッド実証: オンライン498 vs 認識500)。

### q7_conv_plan — オンライン成約の社内計画(ヨミ)件数 [v1.2追加]
ソース: `sheinc_marts_output_spreadsheet_official_monitoring.likes_monthly_online_revenue_forecast_inputs`(VIEW。実体は `sheinc_intermediate.int_likes_online_revenue_forecast_inputs` を `is_current_boundary AND scenario='A'` で絞ったもの)
- 範囲: 前月〜翌月(3行)。
- 出力列: `month` STRING 'YYYY-MM'(=対象月) / `plan_regular` FLOAT(成約数_レギュラー) / `plan_sutara` FLOAT(成約数_スタライ=スタンダード+ライト) / `src_regular` STRING(成約数の出所: '実績'|'ヨミ') / `src_sutara` STRING / `as_of` DATE(計算日_as_of)
- 意味: plan_regular+plan_sutara ≒ オンライン成約(valid)の月間計画。検証済み: 2026-07実績行 637+172=809 ≒ q6ベース7月オンラインvalid実績806。2026-08ヨミ 639+207=846。
- 拠点の計画は上流の `kyoten_regular_conversions`/`kyoten_stali_conversions` に存在するが、参照中のVIEWがオンライン専用のため未取込 → 現状は手入力(targets.json / UI)で扱う。

### q8_yomi — 社内売上ヨミ(オンライン・金額) [v1.2追加]
ソース: 同データセット `likes_monthly_online_revenue_forecast`
- 範囲: 当月〜+2ヶ月。出力列: `month` STRING / `yomi_total` FLOAT(入会金ヨミ+月額ヨミの4成分合計) / `as_of` DATE
- 表示上の扱いは targets.json の `yomi.comparable` フラグに従う(T4が過去ヨミvs FA実績の突合で判定。既定 false=「参考値(定義が財務会計と異なる可能性)」表示)。

### targets.json [v1.2追加] — repo `dashboard/data/targets.json`(コミット対象・人が編集)
```json
{
  "fa_targets": {"lks": null, "mny": null, "pd": null, "total": null},
  "kyoten_conv_target": null,
  "yomi": {"comparable": false, "note": "社内売上ヨミ(オンライン)は財務会計と定義が異なる可能性があるため参考値"},
  "note": "fa_targets は月次の目標FA(円)。null=未設定(UIは前月実績を基準線として表示)。"
}
```
- build.py が DATA.targets として注入。UI: fa_targets 設定時は着地見込みとの差分・達成率を主表示、未設定時は前月実績比を基準に。閲覧者ローカルの一時上書き(localStorage、try/catch必須)可・明示リセット可。
- 成約件数の目標差分(自動) [v1.2.1改定・R4 Important-1裁定]: 当月オンライン = q7 の plan 合計 vs q6 オンライン n_valid 実績。**実績には基準日当日分が含まれるため、成約進捗の残日数は基準日を除く**: convDays = max(0, 残日数−1)。残り必要ペース/日 = max(0, plan−実績) ÷ convDays(convDays=0 のときはゼロ除算せず「残0日」表示)。現ペース着地見込み = 実績 + paceオンライン × convDays。※③(FA)の残日数・シナリオペース定義は従来どおり(凍結回帰に波及させない)。拠点は kyoten_conv_target(またはUI入力)があれば同計算。kyoten_conv_target=0 は「未設定」として扱う。

## v1.3: 通期達成シミュレーション(ユーザー要望「通期達成SIM機能もほしい」)

前提(2026-08-25 実査で確定): SHEの年度は4月〜3月(社内計画テーブルが2027-03で途切れ、年度日数=365)。当年度=2026-04〜2027-03。売上ヨミ(q8ソース)は当月〜2027-03の全月が存在。

### 契約変更
1. **q1範囲拡張**: 下限を「前年度開始(=当年度開始の12ヶ月前)」に変更(現行の当月-12ヶ月より広い。列・意味は不変。行が増えるだけ)。前年度通期実績の算出を可能にする。
2. **q8範囲拡張**: 上限を撤廃し「当月以降に存在する全月」(実質FY末まで)。列不変。
3. **targets.json 追加フィールド**: `"fy_targets": {"total": null, "lks": null, "mny": null, "pd": null}`(当年度の通期目標FA、円) と `"fy_note"`。
4. **logic.js: fySim(純関数)** — 入力: buildModelの結果 + data + simInputs {lksAdjPct(既定0), mnyMonthly(既定=直近3確定月平均), pdMonthly(同), perMonthLksOverride{ym→円}}。
   - 年度月リスト: 4月..3月。各月 status = actual(過去月: q1実績) / current(当月: 着地モデル low/central/high) / future(将来月: 推定)。
   - **LKS将来月推定** = max( q1のbooked_forward(M), yomi_total(M) × bias補正 × (1/オンライン構成比) × (1+lksAdjPct/100) )。bias補正: central=1/1.031、low=1/1.038、high=1/1.026(T4バックテストの+2.6〜3.8%)。オンライン構成比=q4当月構成比(≈0.918)。perMonthLksOverride があればその月はoverride値(band無し)。[v1.3.2] override は q1 の booked_forward(計上済み前受)を下限にクランプし、クランプ時はその旨を表示する(計上済み額を下回る月次FAは論理的にあり得ないため)。yomi欠損月は直近3確定月平均にフォールバック。
   - **MNY/PD将来月推定** = max(q1 booked_forward(M), 編集可能な月次値: 既定=直近3確定月実績平均)。
   - 通期見込み(central/low/high) = Σ実績 + 当月(low/central/high) + Σ将来月(band合成は単純加算)。
   - fy_targets設定時: 達成率、差分、「残り月あたり必要FA」= max(0, 目標−実績−当月central) ÷ 残り将来月数。
   - 前年度通期実績合計(q1から)を比較表示(前年度比)。
5. **UI**: 新セクション「通期達成シミュレーション(2026年度)」— 年度サマリ(見込み・目標比・前年度比)、月次テーブル(実績=確定表記/当月=レンジ/将来=推定・編集可)、調整つまみ(LKS一括%・MNY/PD月次値・月別上書き・リセット)、月次+累積チャート(実績濃・当月レンジ・将来淡、目標線)。将来月は「ヨミ×補正の粗い推定で当月モデルと精度が異なる」旨を明示。localStorage(try/catch・端末のみ表示・出所表示)。
6. 検証: fySimのユニットテスト(目標null/override/欠損yomi/年度境界=4月1日と3月31日/残0将来月)、実績月合計がq1と一致、当月がbuildModelと一致。

## latest.json スキーマ

```json
{
  "generated_at": "2026-08-25T13:05:00+09:00",
  "basis_date": "2026-08-25",
  "target_month": "2026-08",
  "queries": {
    "q1_official_monthly": {"rows": [...]},
    "q2_lks_pending": {"rows": [...]},
    "q3_mny_pd_pending": {"rows": [...]},
    "q4_lks_channel_booked": {"rows": [...]},
    "q5_conv_profile": {"rows": [...]},
    "q6_conv_actuals": {"rows": [...]},
    "q7_conv_plan": {"rows": [...]},
    "q8_yomi": {"rows": [...]}
  },
  "targets": { "...": "targets.json の内容(build.pyが注入)" },
  "meta": {"bytes_processed": {"q1_official_monthly": 3234, "...": 0}}
}
```
rows は型付きオブジェクト(数値は number、NULL は null)。BigQuery REST 形式(rows[].f[].v)からの変換は snapshot 生成時(T1)と、ページのライブ経路(JS の parseBqResult(schema, rows))の双方で同一の結果になること。

## 計算式(logic.js の責務 — 数式はスレッドで実証された手法)

基準: basis_dom(basis_dateの日)、last_dom(当月末日)、残日 = basis_dom..last_dom。

- rate(row) = fa_per_day_cur ?? fa_per_day_prev ?? 0
- **LKS**
  - ①更新残 P1 = Σ_q2 window_days × rate
  - ④滞納/ラグ P4 = Σ_q2 lag_days × rate
  - ②既成約未計上 P2 = Σ_{ch,dom<basis_dom} max(0, n_valid×prof(ch,dom) − booked_fa_valid(ch,dom))
  - ③今後の成約 P3 = Σ_ch pace_ch × Σ_{dom=basis_dom..last_dom} prof(ch,dom)
    - prof(オンライン,dom) = q5オンラインの fa_per_conv(欠損domは0)
    - prof(拠点,dom) = prof(オンライン,dom) × k、k = (拠点の月計 fa_per_conv 加重平均) ÷ (オンライン同) — q5から算出、分母0なら k=0.94(スレッド実務値)
    - prof(分類不能,dom) = prof(オンライン,dom)。pace_分類不能 のデフォルトは 0
  - pending_low = P1+P2+P3 / central = +0.5×P4 / high = +P4(係数0.5は7月実績キャリブレーション由来)
  - landing_* = q1当月lks + pending_*
- **MNY**: P1m, P4m を q3 money 行から同様に。landing = q1当月mny + P1m + w×P4m(low/central/high 同様)。新規成約分は僅少のため未計上(注記表示)。
- **プロデ**: central = q1当月pd(全件一括払いで実質計上済み)。upside = n_window × (前月公式pd ÷ n_active_prev)。high = central + upside。「別パイプラインのため残計上は直接算出不可」caveat を必ず表示。
- **チャネル別(LKS)**: share_ch = q4 booked_fa 構成比(成約記録なし除く)。①②④は share_ch で按分、③は channel 直接。landing_ch = booked_ch + 按分増分。
- **シナリオ入力**: pace_オンライン / pace_拠点 をUIで編集可能。デフォルト = 当月 n_valid 合計 ÷ max(1, basis_dom−1) を小数1桁。変更で③のみ再計算。
- 丸め: 内部はフル精度、表示は百万円(小数1桁)or 円(3桁区切り)。`font-variant-numeric: tabular-nums`。

## 不変条件(test.js と T1検証の両方で確認)

1. Σ q4.booked_fa ≒ q1当月lks(誤差 ±1%以内。スレッドでは完全一致)
2. pending_low ≤ central ≤ high、各成分 ≥ 0
3. q1 の 2026-07 が 8/25検証時点アンカー(lks=356,607,091 / mny=4,619,550 / pd=9,080,741)と**±0.1%以内で一致** [v1.3.1改定: 公式テーブルは日次の修正再計上で過去月が数万円単位でドリフトするため(8/26実測 −0.019%)、完全一致でなく許容誤差付き照合とする。差分は常時表示し、超過はFAIL]
4. MNY の rate が 500〜900円/日の範囲(スレッド実測 658〜706)
5. Σ q6.booked_fa_valid ≤ q1当月lks
6. multicreator の fa_per_day_cur が NULL または 0(別パイプライン仕様の確認)

## 既知の注意点(スレッドからの学び — UI・ドキュメントに反映)

- 公式FAはバッチ更新のため、スプレッドシート認識値と数万〜数十万円ズレることがある(8/20実例: 差¥68,287)。
- 課金更新の1日単価は基準日を後ろにずらすと上がる(月初計上の一括分がearly側に増えるため)→ 毎回再計算が必要(本ダッシュボードは実行時に自動再計算)。
- 予測は構造的に過少に出やすく、④滞納/処理ラグの50%織り込みが7月実績(予測¥349M→実績¥356.6M)由来のキャリブレーション。
- 拠点の per-dom 単価はノイズが大きい → オンラインプロファイル×スケール係数を使う。
- 月末寄りの成約は当月FAがごく僅か(8/31成約は¥0)で、大半は翌月計上。
