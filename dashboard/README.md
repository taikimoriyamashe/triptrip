# 財務売上着地モニター — dashboard/ 実装ガイド

LKS(SHElikes)・MNY(SHEmoney)・プロデ(SHElikes PRO)の当月末FA(財務会計)着地見込みを、BigQueryへの読み取り専用クエリから自動算出するダッシュボードの実装一式。

手法・数式の意味(①〜④、チャネル按分、50%キャリブレーションの根拠など)は `docs/fa-forecast-dashboard.md` を参照。本READMEはディレクトリ構成・データフロー・更新手順・検証・制約という実装面を扱う。

## 概要

- ページはClaude Artifactとして公開する自己完結HTML。
- 閲覧者が「Google Cloud BigQuery」コネクタを持っていれば、ページ内で6本のSQLをその場でライブ実行し、閲覧時点の日付を基準日として再計算する(mcp capability)。
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
│   └── q6_conv_actuals.sql       当月の成約実績(channel×日、有効/無効件数)
├── data/
│   └── latest.json               6クエリの結果を型付きで格納したスナップショット
├── template.html                 /*__DATA__*/ プレースホルダを持つダッシュボードのHTML雛形
├── logic.js                      計算ロジック(純関数・DOM非依存)。スナップショット/ライブ経路共通
├── build.py                      latest.json を template.html に注入し out/dashboard.html を生成
├── test.js                       logic.js の計算式・不変条件のテスト
└── out/
    └── dashboard.html            ビルド成果物。Claude Artifactとして公開する対象
```

各SQLの要点:

- **q1_official_monthly**: ソースは `sheinc_marts_output_spreadsheet_official_monitoring.monthly_financial_accounting`。当月の12ヶ月前〜テーブル上の未来月まで、`year_month`/`lks`/`mny`/`pd` を返す。全計算の起点(「現時点計上済み額」)。
- **q2_lks_pending**: `int_membership_tokens` × `all_orders` に `int_likes_financial_accounting` のFAを突き合わせ、プラン(レギュラー/スタンダード/ライト/卒業生)×支払種別ごとに1日単価・①(未計上の残日数/件数)・④(滞納/処理ラグの残日数/件数)を出す。当月に最初の有効成約をした会員のオーダーは、②③との二重計上を避けるため①④の両方(window集計・early/lag集計)から除外を試みる(`all_orders` にユーザーIDが引けることが前提。引けない場合は除外なしで実装し、その旨をT1の検証結果に明記する)。
- **q3_mny_pd_pending**: q2と同じロジックだが `service_key IN ('money','multicreator')`、FAは `sheinc_marts_accounting.monthly_accounting` から取る。multicreator(プロデ)はこのテーブルにFAが載らない別パイプラインのため、`fa_per_day` がNULL/0になるのが仕様通りの挙動。件数列(`n_window`等)だけが意味を持つ。
- **q4_lks_channel_booked**: `likes_conversions` の入会時(最初の有効成約)`trial_lesson_type` から `channel`(オンライン/拠点/分類不能)を判定し、`int_likes_financial_accounting` の当月FAをchannel別に集計する。成約記録が無いユーザーは「成約記録なし」。
- **q5_conv_profile**: 前月に最初の有効成約をした会員について、成約日(dom)×channelごとの件数と1件あたり前月FA平均を出す。②・③の単価カーブの元になる。
- **q6_conv_actuals**: 当月の成約実績をchannel×domで集計し、全件数・有効件数(`is_valid_conversions`)・有効成約者の当月計上済みFAを返す。シナリオ入力のデフォルトペース算出と、事業側の件数認識との突き合わせに使う。

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
    "q6_conv_actuals": {"rows": [...]}
  },
  "meta": {"bytes_processed": {"q1_official_monthly": 3234, "...": 0}}
}
```

`rows` は型付きオブジェクト(数値はnumber、NULLはnull)。BigQuery REST形式(`rows[].f[].v`)からの変換は、スナップショット生成時とページのライブ経路(`logic.js` が提供する、BigQuery RESTレスポンス→型付き行配列に変換する関数)の双方で同一の結果になるようにする。

## データフロー図

```
[BigQuery: shelikes-001]  (読み取り専用 SELECT ×6、CURRENT_DATE('Asia/Tokyo')基準・パラメータ不要)
        │
        ▼
sql/q1_official_monthly.sql ─┐
sql/q2_lks_pending.sql       │
sql/q3_mny_pd_pending.sql    ├─▶ data/latest.json (型付きスナップショット)
sql/q4_lks_channel_booked.sql│         │
sql/q5_conv_profile.sql      │         │ build.py が template.html の
sql/q6_conv_actuals.sql ─────┘         │ /*__DATA__*/ プレースホルダに注入
                                        ▼
                              out/dashboard.html
                                        │
                                        │ Claude Artifactとして公開
                                        ▼
                    ┌───────────────────┴───────────────────┐
                    │                                       │
      閲覧者がBigQueryコネクタを持つ場合              持たない場合
                    │                                       │
   ページ内でq1〜q6を同一SQLでライブ再実行          埋め込み済みlatest.jsonを表示
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

1. `sql/` 配下の6クエリを `shelikes-001` プロジェクト(読み取り専用)に対して実行する。`CURRENT_DATE('Asia/Tokyo')` 基準で自己完結しているため、パラメータの変更は不要。
2. 結果を `latest.json` のスキーマ(`generated_at`/`basis_date`/`target_month`/`queries.*.rows`/`meta.bytes_processed`)に変換して `data/latest.json` を更新する。BigQuery REST形式(`rows[].f[].v`)からの変換は `logic.js` の `parseBqResult` と同一ロジックを使う。
3. `build.py` を実行して `out/dashboard.html` を再生成する。
4. `test.js` を実行し、下記「検証」の不変条件がすべて満たされることを確認する。失敗した場合は生成物を公開せず、原因を調査する。
5. `out/dashboard.html` の内容でArtifactを再公開する(同一URLへの再デプロイ)。
6. (日次Routineの場合)次回実行時刻まで待機する。

- 公開URL: {{ARTIFACT_URL}}
- 更新Routine: {{TRIGGER_INFO}}

### 人間が手動で行う場合

1. BigQueryコンソールまたは `bq` コマンドで、`sql/` 配下の6本のSQLを `shelikes-001` プロジェクトに対して実行する(要・認証情報)。
2. 各クエリ結果を `latest.json` のスキーマに合わせて整形し、`data/latest.json` を更新する。
3. `python build.py` を実行して `out/dashboard.html` を生成する。
4. `node test.js`(またはリポジトリのテスト実行手順)で不変条件を確認する。
5. `out/dashboard.html` の内容を、既存のArtifact URL({{ARTIFACT_URL}})に再公開する、または当該URLを管理しているClaudeセッションに更新を依頼する。

## 検証(test.js と不変条件)

`test.js` は、データ契約で定義された次の不変条件を確認する。

1. Σ `q4.booked_fa` ≒ q1当月の `lks`(誤差±1%以内)
2. `low ≤ central ≤ high`、各成分は0以上
3. q1の2026-07が `lks=356,607,091` / `mny=4,619,550` / `pd=9,080,741` と一致(固定の回帰チェック)
4. MNYの1日単価(`rate`)が500〜900円/日の範囲内(スレッド実測は658〜706円/日)
5. Σ `q6.booked_fa_valid` ≤ q1当月の `lks`
6. `multicreator` の `fa_per_day_cur` がNULLまたは0(別パイプライン仕様であることの確認)

これらは `logic.js` の純関数に対して直接テストできるため、DOM描画やBigQuery接続なしでCIから実行できる。フィクスチャ(実データのスナップショット)が無い環境では、契約に対する不変条件テストはスキップし、合成データに対する計算式のテストのみ実行する設計になっている。

## 制約

- 全SQLは読み取り専用(SELECT文のみ)。更新・DDLは一切発行しない。BigQuery scripting(`DECLARE`)は使用不可 — 単一SELECT文(`WITH`可)のみ。
- コスト目安: Slackスレッドでの都度のDevin依頼は、1メッセージ(本体回答またはナレッジ追記)あたり実測¥480〜¥1,760(1.5〜5.5 ACU)を消費し、これに加えて数分〜10分の待ち時間が発生していた。1回の依頼で複数メッセージ(本体回答+ナレッジ追記等)が発生する場合はコストが合算になる(例: 2026-08-11の依頼は本体回答¥1,300+ナレッジ追記¥1,760=合計約¥3,060)。本ダッシュボードはこれを、閲覧のみ(スナップショット経路)またはBigQueryのクエリ課金のみ(ライブ経路)に置き換える。クエリ課金(bytes_processed)は `latest.json` の `meta` に記録する。
- 閲覧者がBigQueryコネクタを持たない場合、表示されるのは直近の日次スナップショット時点の数値であり、リアルタイムではない。
- 数値はあくまで着地予測であり確定値ではない。手法・限界の詳細は `docs/fa-forecast-dashboard.md` を参照。
