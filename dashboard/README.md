# 財務売上着地モニター — dashboard/ 実装ガイド

LKS(SHElikes)・MNY(SHEmoney)・プロデ(SHElikes PRO)の当月末FA(財務会計)着地見込みを、BigQueryへの読み取り専用クエリから自動算出するダッシュボードの実装一式。

手法・数式の意味(①〜④、チャネル按分、50%キャリブレーションの根拠など)は `docs/fa-forecast-dashboard.md` を参照。本READMEはディレクトリ構成・データフロー・更新手順・検証・制約という実装面を扱う。

## 概要

- ページはClaude Artifactとして公開する自己完結HTML。
- 閲覧者が「Google Cloud BigQuery」コネクタを持っていれば、ページ内の「ライブ更新」ボタンで8本のSQLをその場で再実行し、閲覧時点の日付を基準日として再計算できる(mcp capability。閲覧しただけではクエリは実行されない)。
- コネクタを持たない閲覧者には、ビルド時に埋め込まれた最新スナップショットを表示する。
- 計算ロジックはすべて `logic.js` の純関数(DOM非依存)に集約されており、ライブ経路・スナップショット経路の両方が同じ関数を通る。これにより2経路の計算結果が常に一致する。

## ディレクトリ構成

```
dashboard/
├── sql/
│   ├── q1_official_monthly.sql   公式月次FA(計上済み実績)
│   ├── q2_lks_pending.sql        LKS 既存会員の課金更新残・滞納/処理ラグ(プラン×支払種別)
│   ├── q3_mny_pd_pending.sql     MNY・プロデ 同等集計(service_key別)
│   ├── q4_lks_channel_booked.sql LKS 計上済みFAのチャネル(オンライン/拠点)按分
│   ├── q5_conv_profile.sql       成約日別・1件あたり当月FA プロファイル(前月実測)
│   ├── q6_conv_actuals.sql       当月の成約実績(channel×日、有効/無効件数)
│   ├── q7_conv_plan.sql          オンライン成約の社内計画(ヨミ)件数 [v1.2]
│   └── q8_yomi.sql               社内売上ヨミ(オンライン・金額) [v1.2]
├── build_snapshot.py             生のBigQuery RESTレスポンス(raw/q*.json)→ data/latest.json への型付き変換+targets注入+不変条件チェッカー
├── raw/
│   └── q*.json                   8クエリの生BigQuery RESTレスポンス(最終更新時の断面)
├── data/
│   ├── latest.json               8クエリの結果を型付きで格納したスナップショット
│   └── targets.json              月次FA目標・拠点成約目標・ヨミ比較可否フラグ(リポジトリ管理・人が編集する正)[v1.2]
├── testdata/
│   └── snapshot-2026-08-25.json  回帰テスト専用の凍結スナップショット(日次更新の影響を受けない)
├── template.html                 /*__DATA__*/ プレースホルダを持つダッシュボードのHTML雛形
├── logic.js                      計算ロジック(純関数・DOM非依存)。スナップショット/ライブ経路共通
├── build.py                      latest.json を template.html に注入し out/dashboard.html を生成
├── test.js                       logic.js の計算式・不変条件のテスト
└── out/
    └── dashboard.html            ビルド成果物。Claude Artifactとして公開する対象
```

`build_snapshot.py` は、8クエリ実行で得た生のBigQuery RESTレスポンス(`raw/q1_official_monthly.json` 等)を読み込み、型付き変換を行って `data/latest.json` を生成し、`dashboard/data/targets.json` の内容を `targets` として注入し、続けてデータ契約の不変条件チェック(v1.2で追加した「q7前月実績 vs q5実績」の整合チェック[7]を含む)を実行するスクリプト。次の2つのガードを持つ: (1) JST 00:00〜00:10の実行を中断する(月境界レースの回避)、(2) 生レスポンスの取得月と基準日の月が一致しない場合に中断する。

各SQLの要点:

- **q1_official_monthly**: ソースは `sheinc_marts_output_spreadsheet_official_monitoring.monthly_financial_accounting`。範囲は前年度開始(=当年度開始の12ヶ月前)〜テーブル上の未来月まで[v1.3で拡張。従来は当月の12ヶ月前まで]、`year_month`/`lks`/`mny`/`pd` を返す。全計算の起点(「現時点計上済み額」)であり、通期達成シミュレーション(前年度通期実績の算出・当年度実績月の確定値)にも使う。
- **q2_lks_pending**: `int_membership_tokens` × `all_orders` に `int_likes_financial_accounting` のFAを突き合わせ、プラン(レギュラー/スタンダード/ライト/卒業生)×支払種別ごとに1日単価・①(未計上の残日数/件数)・④(滞納/処理ラグの残日数/件数)を出す。当月に最初の有効成約をした会員のオーダーは、②③との二重計上を避けるため①④の両方(window集計・early/lag集計)から除外する(`all_orders` のユーザーIDで紐付け。実装・検証済み — 2026-08-25実測の除外規模: window側80件・約¥36万円/lag側45件・約¥62万円)。
- **q3_mny_pd_pending**: q2と同じロジックだが `service_key IN ('money','multicreator')`、FAは `sheinc_marts_accounting.monthly_accounting` から取る。multicreator(プロデ)はこのテーブルにFAが載らない別パイプラインのため、`fa_per_day` がNULL/0になるのが仕様通りの挙動。件数列(`n_window`等)だけが意味を持つ。
- **q4_lks_channel_booked**: `likes_conversions` の入会時(最初の有効成約)`trial_lesson_type` から `channel`(オンライン/拠点/分類不能)を判定し、`int_likes_financial_accounting` の当月FAをchannel別に集計する。成約記録が無いユーザーは「成約記録なし」。
- **q5_conv_profile**: 前月に最初の有効成約をした会員について、成約日(dom)×channelごとの件数と1件あたり前月FA平均を出す。②・③の単価カーブの元になる。
- **q6_conv_actuals**: 当月の成約実績をchannel×domで集計し、全件数・有効件数(`is_valid_conversions`)・有効成約者の当月計上済みFAを返す。シナリオ入力のデフォルトペース算出と、事業側の件数認識との突き合わせに使う。
- **q7_conv_plan** [v1.2]: ソースは `likes_monthly_online_revenue_forecast_inputs`(VIEW。実体は `sheinc_intermediate.int_likes_online_revenue_forecast_inputs` を `is_current_boundary AND scenario='A'`=Aヨミで絞ったもの)。前月〜翌月の3行を返し、月ごとにオンライン成約の社内計画件数(レギュラー/スタライ)と出所(`src_regular`/`src_sutara` = '実績'|'ヨミ')を持つ(2026年8月はレギュラー639件+スタライ207件=846件)。拠点の計画は上流テーブルの `kyoten_regular_conversions`/`kyoten_stali_conversions` に存在するが、このVIEWがオンライン専用のため未取込(現状は`targets.json`または画面入力で代替。上流を直接参照すれば自動化可能)。追加スキャンコストは実質ゼロ(実測13.6KB)。
- **q8_yomi** [v1.2]: ソースは同データセットの `likes_monthly_online_revenue_forecast`。範囲は当月以降に存在する全月(実質FY末=3月まで)[v1.3で上限を撤廃。従来は当月〜+2ヶ月]のオンライン売上ヨミ(入会金ヨミ+月額ヨミの4成分合計)を返す。表示上の扱いは `targets.json` の `yomi.comparable` フラグに従う(既定false=参考値表示。バックテストで財務会計実績比+2.6〜3.8%過大と判定されたため)。通期達成シミュレーションのLKS将来月推定にも使う。追加スキャンコストは実質ゼロ(実測3.1KB)。

SQLはすべて `CURRENT_DATE('Asia/Tokyo')` 基準で自己完結しており、パラメータは不要(いつ実行しても当月が対象になる)。BigQuery scripting(`DECLARE`)は使えないため単一SELECT文(`WITH`可)のみで書く。列エイリアスはそのまま `latest.json` のキーになるため、SQLを変更する場合は必ず `logic.js` と `test.js` を同期させること。

`data/latest.json` のスキーマ:

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
  "targets": { "...": "targets.json の内容(build_snapshot.pyが注入)" },
  "meta": {"bytes_processed": {"q1_official_monthly": 3234, "...": 0}}
}
```

`rows` は型付きオブジェクト(数値はnumber、NULLはnull)。BigQuery REST形式(`rows[].f[].v`)からの変換は、スナップショット生成時とページのライブ経路(`logic.js` が提供する、BigQuery RESTレスポンス→型付き行配列に変換する関数)の双方で同一の結果になるようにする。

## データフロー図

```
[BigQuery: shelikes-001]  (読み取り専用 SELECT ×8、CURRENT_DATE('Asia/Tokyo')基準・パラメータ不要)
        │
        ▼
sql/q1_official_monthly.sql ─┐
sql/q2_lks_pending.sql       │
sql/q3_mny_pd_pending.sql    │
sql/q4_lks_channel_booked.sql├─▶ raw/q*.json ─▶ build_snapshot.py ─▶ data/latest.json
sql/q5_conv_profile.sql      │   (生レスポンス)  (型付き変換+検証     (+ data/targets.json
sql/q6_conv_actuals.sql      │                    +targets注入)        を注入)
sql/q7_conv_plan.sql         │         │
sql/q8_yomi.sql ─────────────┘         │ build.py が template.html の
                                        │ /*__DATA__*/ プレースホルダに注入
                                        ▼
                              out/dashboard.html
                                        │
                                        │ Claude Artifactとして公開
                                        ▼
                    ┌───────────────────┴───────────────────┐
                    │                                       │
      閲覧者がBigQueryコネクタを持つ場合              持たない場合
                    │                                       │
   ページ内でq1〜q8を同一SQLでライブ再実行          埋め込み済みlatest.jsonを表示
   (基準日=閲覧時点の日付)                                   │
                    │                                       │
                    └─────────────────┬─────────────────────┘
                                       ▼
                     logic.js の同一関数で①〜④・チャネル按分・
                     low/central/highレンジを計算
                     (シナリオ入力=成約ペースの変更は③のみ再計算)
```

## 更新runbook

### Claudeセッションが行う場合(自動)

1. `sql/` 配下の8クエリを `shelikes-001` プロジェクト(読み取り専用)に対して実行する。`CURRENT_DATE('Asia/Tokyo')` 基準で自己完結しているため、パラメータの変更は不要。**8クエリは必ず1パスでまとめて取得し、クエリ間で時間を空けないこと**(基準日の断面の一貫性を保つため)。
2. 各クエリの生のBigQuery RESTレスポンスを保存する(`raw/q1_official_monthly.json` 等、計8ファイル)。
3. `build_snapshot.py` を実行する。生レスポンスを `latest.json` のスキーマ(`generated_at`/`basis_date`/`target_month`/`queries.*.rows`/`targets`/`meta.bytes_processed`)に型付き変換して `data/latest.json` を生成し(変換ロジックは `logic.js` の型変換処理と同一)、`dashboard/data/targets.json` の内容を `targets` として注入し、続けてデータ契約の不変条件チェック(v1.2で追加した「q7前月実績 vs q5実績」の整合チェック[7]を含む)を実行して結果を出力する(下記「検証」参照)。**JST深夜0時台(00:00〜00:10)の実行は避けること**(月境界レース回避のガードにより中断される)。生レスポンスの取得月と基準月が一致しない場合も中断される。
4. `build.py` を実行して `out/dashboard.html` を再生成する。
5. `out/dashboard.html` の内容でArtifactを再公開する(同一URLへの再デプロイ)。
6. (日次Routineの場合)次回実行時刻まで待機する。

- 公開URL: https://claude.ai/code/artifact/35c78380-ed5b-4438-8c85-4f928347034f
- 更新Routine: 「財務売上着地モニター 日次更新」(trig_01SQQKgGtHEYaLjLrWRGpe4d、毎日 08:05 JST に元のClaudeセッションを起こして再取得・再公開)。※起こされたセッションでBigQueryコネクタが使えない場合は再ベイクをスキップする縮退設計(その場合もページを開けばライブ再計算される)。停止はclaude.aiのルーチン管理画面から可能。

### 人間が手動で行う場合

1. BigQueryコンソールまたは `bq` コマンドで、`sql/` 配下の8本のSQLを `shelikes-001` プロジェクトに対して実行する(要・認証情報)。**8クエリは必ず1パスでまとめて取得し、クエリ間で時間を空けないこと**(基準日の断面の一貫性を保つため)。
2. 各クエリの生のBigQuery RESTレスポンスを保存する(`raw/q1_official_monthly.json` 等、計8ファイル)。
3. `python build_snapshot.py` を実行して `data/latest.json` を生成する(`dashboard/data/targets.json` の注入とデータ契約の不変条件チェックも同時に実行される)。**JST深夜0時台(00:00〜00:10)の実行は避けること**(月境界レース回避のガードにより中断される)。
4. `python build.py` を実行して `out/dashboard.html` を生成する。
5. `out/dashboard.html` の内容を、既存のArtifact URL(https://claude.ai/code/artifact/35c78380-ed5b-4438-8c85-4f928347034f)に再公開する、または当該URLを管理しているClaudeセッションに更新を依頼する。

## 目標の設定方法 [v1.2]

月次のFA目標・拠点の成約目標は `dashboard/data/targets.json`(リポジトリ管理・チーム共通の正)で設定する。スキーマ:

```json
{
  "fa_targets": {"lks": null, "mny": null, "pd": null, "total": null},
  "kyoten_conv_target": null,
  "yomi": {"comparable": false, "note": "社内売上ヨミ(オンライン)は財務会計と定義が異なるため参考値。..."},
  "fy_targets": {"total": null, "lks": null, "mny": null, "pd": null},
  "fy_note": "fy_targets は当年度(4月〜3月)の通期目標FA(円)。null=未設定(UIは前年度通期実績を基準線として表示)。",
  "note": "fa_targets は月次の目標FA(円)。null=未設定(UIは前月実績を基準線として表示)。"
}
```

- `fa_targets.lks`/`mny`/`pd`/`total`: 各サービスの月次FA目標額(円)。`null`(未設定)の項目は、ダッシュボード側で前月実績を仮の基準線として表示する。
- `kyoten_conv_target`: 拠点の当月成約目標件数。拠点の計画は上流テーブルに存在するものの現行クエリが取り込んでいないための代替設定値(オンラインの計画は `q7_conv_plan` から自動取得するため設定不要)。
- `yomi.comparable`: 社内売上ヨミをFA目標として比較表示してよいかのフラグ。既定 `false`(バックテストでオンラインFA実績比+2.6〜3.8%の一貫した過大バイアスが確認されているため)。判定根拠は同フィールドの `note` に記録する。
- `fy_targets.total`/`lks`/`mny`/`pd` [v1.3]: 当年度(2026年4月〜2027年3月)の通期目標FA(円)。現在は全項目 `null`(未設定)。設定すると通期達成シミュレーションで達成率・差分・「残り将来月あたり必要FA」を表示する。未設定時は前年度通期実績(FY2025合計¥4,354,284,641)との比較のみになる。

**編集手順**:

1. `dashboard/data/targets.json` を直接編集し、値を入れる(`null` のままなら未設定=前月実績・前年度通期実績を基準線として扱う)。
2. `python build_snapshot.py` → `python build.py` の順に実行し、`out/dashboard.html` を再生成する(`build_snapshot.py` が `targets.json` の内容を `data/latest.json` の `targets` に注入する)。
3. `out/dashboard.html` の内容でArtifactを再公開する。
4. 上記1〜3は、Claudeセッションに依頼すればまとめて実行してもらえる(例:「LKSの目標を¥3.6億に設定して」「通期目標を¥42億に設定して」)。

**画面編集で使うlocalStorageキー** [v1.3で一覧化]:

- `fa-monitor-targets-v1`: 月次目標(`fa_targets`)・通期目標(`fy_targets`)の画面上書き値。リポジトリの値との差分のみを保持し、値ごとの出所(`origin`: リポジトリ値かローカル上書きか)を記録する。
- `fa-monitor-fysim-v1`: 通期達成シミュレーションの調整値(LKS一括調整%・LKS月別上書き・MNY/プロデの月次値)。

いずれもその端末のブラウザにのみ保存され、`targets.json`(リポジトリの正)には反映されない。チーム全体に共有する目標・前提にする場合は、上記手順で `targets.json` 自体を更新すること。

## 検証(test.js と不変条件)

`test.js` は、データ契約で定義された次の不変条件を確認する。

1. Σ `q4.booked_fa` ≒ q1当月の `lks`(誤差±1%以内)
2. `low ≤ central ≤ high`、各成分は0以上
3. q1の2026-07が `lks=356,607,091` / `mny=4,619,550` / `pd=9,080,741` と±0.1%以内で一致(固定の回帰チェック。[v1.3.1改定] 公式テーブルは日次の修正再計上で過去月が数万円単位でドリフトするため、完全一致ではなく許容誤差付きで照合する。差分は常時表示し、超過時はFAILとする。8/26実測: 7月lksの差分−69,493円)
4. MNYの1日単価(`rate`)が500〜900円/日の範囲内(スレッド実測は658〜706円/日)
5. Σ `q6.booked_fa_valid` ≤ q1当月の `lks`
6. `multicreator` の `fa_per_day_cur` がNULLまたは0(別パイプライン仕様であることの確認)

これらは `logic.js` の純関数に対して直接テストできるため、DOM描画やBigQuery接続なしでCIから実行できる。フィクスチャ(実データのスナップショット)が無い環境では、契約に対する不変条件テストはスキップし、合成データに対する計算式のテストのみ実行する設計になっている。

## 制約

- 全SQLは読み取り専用(SELECT文のみ)。更新・DDLは一切発行しない。BigQuery scripting(`DECLARE`)は使用不可 — 単一SELECT文(`WITH`可)のみ。
- コスト目安: Slackスレッドでの都度のDevin依頼は、1メッセージ(本体回答またはナレッジ追記)あたり実測¥480〜¥1,760(1.5〜5.5 ACU)を消費し、これに加えて数分〜10分の待ち時間が発生していた。1回の依頼で複数メッセージ(本体回答+ナレッジ追記等)が発生する場合はコストが合算になる(例: 2026-08-11の依頼は本体回答¥1,300+ナレッジ追記¥1,760=合計約¥3,060)。本ダッシュボードはこれを、閲覧のみ(スナップショット経路)またはBigQueryのクエリ課金のみ(ライブ経路)に置き換える。クエリ課金(bytes_processed)は `latest.json` の `meta` に記録する。
- 閲覧者がBigQueryコネクタを持たない場合、表示されるのは直近の日次スナップショット時点の数値であり、リアルタイムではない。
- 数値はあくまで着地予測であり確定値ではない。手法・限界の詳細は `docs/fa-forecast-dashboard.md` を参照。
