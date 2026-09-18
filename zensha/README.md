# 全社着地モニター — zensha/ 実装ガイド（毎朝の自動更新）

全社（SHElikes / SHEmoney / PROデザイナー / 法人 / グロースタジオ）の売上着地と SHElikes の成約KPIを、
3つの Google スプレッドシートから毎朝自動で再生成し、Claude Artifact として再公開するダッシュボードの実装一式。

- 公開URL（森山所有・非公開）: **https://claude.ai/artifact/9EaQMq5gPPWbQh7jrF4CqC**
- 元になったページ: 「全社着地モニター」（他者所有・共有閲覧のみ。`reference/original-2026-09-08.html` に断面を保存）
- 更新Routine: **「全社着地モニター 日次更新」**（`trig_012vppYLXi9KcobvdSUqn5uS`、cron `30 0 * * *` = **毎朝 09:30 JST**）。3シートの最終更新が 08:40〜09:10 JST に入るため、その後に回す。停止・時刻変更は claude.ai のルーチン管理画面から。
  - このRoutineは **既存セッションを起こす方式**（`persistent_session_id` 固定）。Routine自体にはコネクタが保存されていない（この組織ではRoutineへのコネクタ付与が使えない）ため、**起こされたセッションで Google Drive が使えなければ取得できない**。その場合は何も書き換えずに報告して終わる設計（`BLOCKED:DRIVE_UNAVAILABLE`）。恒久的に直すなら claude.ai のルーチン画面から Google Drive コネクタ付きで作り直す。

## データの流れ

```
[Google Sheets]                              [Claude セッション（Routine が毎朝起動）]
 FY26_経営モニタリング ─┐   Google Drive MCP        ingest.py           extract.py            build.py
 26年9月_マーケジスイ… ├──▶ read_file_content ──▶ raw/*.md ──▶ data/latest.json ──▶ out/zensha.html ──▶ Artifact 再公開
 FY26_拠点モニタリング ─┘   (結果はファイル保存)                 ▲ 不変条件検査          ▲ data/manual.json（人手）
```

- `raw/*.md` … Drive MCP が返す Markdown 表ダンプ（タブ名なし・空行区切り）。**表は見出し文字列で特定**する（ブロック番号は使わない）。
- `data/latest.json` … 契約 `CONTRACT.md` に従う抽出結果。売上（財務会計/管理会計 × 5サービス × 12ヶ月の 期初計画・実績/Aヨミ）、単月確認用、SHElikes（オンライン/拠点）の 目標・Aヨミ・実績・日次・ファネル・広告費・拠点別CPA。
- `data/manual.json` … 人が保守する部分。経営判断が必要な事項／現場で対応中／注記／SHEmoney・PRO の仮KPI。`updated_at` を更新日に。
- `template.html` … 原本の見た目そのまま。`/*__DATA__*/`（と必要なら `/*__LOGIC__*/`）に build.py が注入。
- `out/zensha.html` … 公開する成果物。

## 更新 runbook（Claude セッションが毎朝行う）

前提: このリポジトリの branch `claude/awesome-ride-r36g1y` をチェックアウトし最新化（`git fetch origin claude/awesome-ride-r36g1y && git checkout claude/awesome-ride-r36g1y && git pull --ff-only`）。

1. **取得** — Google Drive MCP `read_file_content` を3ファイルに対して呼ぶ（ID は `CONTRACT.md` の「入力」節）。
   - マーケジスイは月ごとにファイルが変わる。当月分（例: 「26年10月_マーケジスイ進捗管理表」）を `search_files` の `title contains 'マーケジスイ進捗管理表'` で探し、**当月のもの**を選ぶ。
   - 結果は大きいためツール結果がファイルに保存される。そのパスを `python3 zensha/ingest.py <path> keiei|marke|kyoten` に渡して `raw/*.md` を更新する。
2. **抽出** — `python3 zensha/extract.py`。不変条件（契約参照）NG なら非0で止まる。**NGのときは公開しない**。
3. **検証** — `python3 zensha/test_extract.py` と `node zensha/test_build.js`。
4. **生成** — `python3 zensha/build.py`（→ `out/zensha.html`）。
5. **公開** — Artifact ツールで、まず `action:"read"` に公開URL（下記）を渡して現行版を読み、次に **`url` を指定して** `zensha/out/zensha.html` を publish する。
   「同じ file_path で再公開すればURLが維持される」のは**同一会話内だけ**で、毎朝は別セッションになるため、必ず `url` を渡すこと。`url` を渡さない publish は別Artifactを増やす。`icon` は初回だけで以後は省略（アイコンを保つため）。
6. **記録** — `git add zensha && git commit -m "全社着地モニター 日次更新 <YYYY-MM-DD>"` → `git push -u origin claude/awesome-ride-r36g1y`。
7. **異常時のみ報告** — 次のときだけ要点を報告し、公開もコミットもしない。正常時は静かに完了する。
   - 抽出が非0（不変条件NG）、テストが FAIL
   - Drive が読めない・権限エラー（`BLOCKED:DRIVE_UNAVAILABLE`）、当月のマーケジスイが見つからない（`BLOCKED:MARKE_NOT_FOUND`）
   - 全社の通期着地見込みが前日から ±5% 超動いた。前日値は `git show HEAD:zensha/data/latest.json` から取る
   - `extract_log` に前日に無かった「警告」が出た
   手入力（manual.json）の鮮度警告と、期限切れの判断事項は、公開は通常どおり行ったうえで1行だけ報告に含める。

### 異常時の見当
- **「〜が見つかりません」で止まる** → Drive のダンプが途中で切れている可能性が高い。3本とも1MB前後で**行の途中で終わっている**（取得APIの上限）。月が進んで日次表の行が増えると、必要な表が切断点の外に出て突然止まる。その場合はシート全体ではなくタブ/範囲を絞って取得する方式への切り替えが要る。
- **不変条件で止まる** → `extract_log` と stderr に理由が日本語で出る。シート側の構造変更（行の並べ替え・列の追加）が原因のことが多い。`EXTRACT_NOTES.md` の「壊れやすいところ」を参照。
- **Routine が発火していない** → 一番起きやすい失敗なのに「何も起きない」ので気づきにくい。画面の「N日前のデータ」バッジが唯一の手掛かり。2日以上バッジが出ていたら Routine の状態を確認する。

### 月替わりの注意
- `target_month` は **marke（マーケジスイ）の日次表の日付行**から決まる（JST の今日の月ではない）。したがって月初に marke がまだ前月版のままだと、その月を対象として処理される。extract.py はこれを検知して非0終了する（stale ガード）。前月を締めるときは `--today <前月末日>` を使う。
- マーケジスイのファイル名（YY年M月）が変わる。見つからなければ BLOCKED として報告。
- **`ingest.py` を必ず通すこと**。ingest.py が書く `raw/<name>.meta.json` に当月シートの id が入る。これを経ないと画面の「元データ」リンクが前月のシートを指すことがある（extract.py が警告する）。
- **年度替わり（2027-04）**: 不変条件3 の基準値（原本 FY26 の期初計画）が FY27 には無い。extract.py は警告に降格して動くが、`CONTRACT.md` と `extract.py: ORIG_PLAN` に FY27 の期初計画を登録するまで、期初計画の破損は検知できない。
- 実績確定月（keiei「実績確定月→」）が進むと月次予実の実績/ヨミ境目が自動で動く。

## 人が手で行う場合
1. 3シートを開き、Drive から Markdown/CSV でエクスポートする代わりに、Claude セッションに「全社着地モニターを更新して」と依頼する（この README の runbook を実行する）。
2. 文言（判断事項・注記）を変えたいときは `data/manual.json` を編集して push し、次回更新を待つか、その場で更新を依頼する。

### `data/manual.json` を編集するときの約束
- **数値をベタ書きしない**。消化率・CPA・件数・残日数などシートから導ける値は `{{kyoten.spend_rate}}` のような**トークン**で書く。使えるトークンの一覧と書き方は `data/manual.json` 冒頭の `_readme` を参照。ベタ書きすると、翌朝には画面の他の場所と矛盾する（判断事項だけが古い数字のまま残る）。
- 編集したら `updated_at` を更新日にする。`basis_date` と7日以上離れると、画面に「N日前の手入力です」の警告バッジが出る。
- `decisions[].due`（期限）と `field_notes.kyoten.extra`（現場の所感）は**人が決める値**なのでトークン化していない。期限切れの日付が残っていないか、更新時に確認する。
- 本文は `<b>` などの生HTMLが効く（**信頼できる入力**として扱っている）。一方 `data/latest.json` はシート由来の外部入力なので、画面側で全てエスケープしている。manual.json に外部から受け取った文字列を貼らないこと。

### 成果物の置き場所
- `out/zensha.html` … **実データ版**。これを Artifact として公開する（`python3 zensha/build.py` の既定出力）。
- `testdata/out-fixture.html` … 原本(2026-09-08)フィクスチャでのビルド。回帰確認用で、公開しない。

## ディレクトリ
```
zensha/
├── CONTRACT.md            データ契約（latest.json / manual.json）
├── README.md              本書
├── EXTRACT_NOTES.md       各値の出所（どの表・どの行列）と補正
├── ingest.py              MCP結果 → raw/*.md
├── extract.py             raw/*.md → data/latest.json（不変条件つき）
├── test_extract.py        抽出の回帰・不変条件テスト
├── build.py               latest.json + manual.json → out/zensha.html
├── template.html          原本由来のテンプレート
├── logic.js               （あれば）描画計算の純関数群
├── test_build.js          描画計算・原本再現テスト
├── data/{latest.json, manual.json}
├── testdata/              凍結フィクスチャ（原本 2026-09-08）
├── raw/                   直近ダンプ（keiei.md / marke.md / kyoten.md）
├── reference/             原本HTML・調査用スクリプト
└── out/zensha.html        公開成果物
```
