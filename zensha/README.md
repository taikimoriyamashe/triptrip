# 全社着地モニター — zensha/ 実装ガイド（毎朝の自動更新）

全社（SHElikes / SHEmoney / PROデザイナー / 法人 / グロースタジオ）の売上着地と SHElikes の成約KPIを、
3つの Google スプレッドシートから毎朝自動で再生成し、Claude Artifact として再公開するダッシュボードの実装一式。

- 公開URL（森山所有）: **（T3で確定後に追記）**
- 元になったページ: 「全社着地モニター」（他者所有・共有閲覧のみ。`reference/original-2026-09-08.html` に断面を保存）
- 更新Routine: **（T3で確定後に追記）**。毎朝 JST にこのセッションを起こし、下記 runbook を実行する。

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
5. **公開** — Artifact ツールで `zensha/out/zensha.html` を **同じ file_path で再公開**（URL 維持。icon は初回のみ指定、以後省略）。
6. **記録** — `git add zensha && git commit -m "全社着地モニター 日次更新 <YYYY-MM-DD>"` → `git push -u origin claude/awesome-ride-r36g1y`。
7. **異常時のみ報告** — 抽出NG／テスト失敗／前日比で全社通期着地が ±5% 超動いた／Drive が読めない（BLOCKED:DRIVE_UNAVAILABLE）は要点だけ報告し、公開・コミットはしない。正常時は静かに完了。

### 月替わりの注意
- `target_month` は **marke（マーケジスイ）の日次表の日付行**から決まる（JST の今日の月ではない）。したがって月初に marke がまだ前月版のままだと、その月を対象として処理される。extract.py はこれを検知して非0終了する（stale ガード）。前月を締めるときは `--today <前月末日>` を使う。
- マーケジスイのファイル名（YY年M月）が変わる。見つからなければ BLOCKED として報告。
- **`ingest.py` を必ず通すこと**。ingest.py が書く `raw/<name>.meta.json` に当月シートの id が入る。これを経ないと画面の「元データ」リンクが前月のシートを指すことがある（extract.py が警告する）。
- **年度替わり（2027-04）**: 不変条件3 の基準値（原本 FY26 の期初計画）が FY27 には無い。extract.py は警告に降格して動くが、`CONTRACT.md` と `extract.py: ORIG_PLAN` に FY27 の期初計画を登録するまで、期初計画の破損は検知できない。
- 実績確定月（keiei「実績確定月→」）が進むと月次予実の実績/ヨミ境目が自動で動く。

## 人が手で行う場合
1. 3シートを開き、Drive から Markdown/CSV でエクスポートする代わりに、Claude セッションに「全社着地モニターを更新して」と依頼する（この README の runbook を実行する）。
2. 文言（判断事項・注記）を変えたいときは `data/manual.json` を編集して push し、次回更新を待つか、その場で更新を依頼する。

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
