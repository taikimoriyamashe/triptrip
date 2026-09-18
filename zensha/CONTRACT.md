# 全社着地モニター — データ契約 v1（zensha/data/latest.json）

対象: `zensha/reference/original-2026-09-08.html`（元ダッシュボード。JS内の REV_ALL / DAILY / STEP / LANE / CAUSE と、HTML内の固定文言）を
「毎朝、3つのGoogleスプレッドシートから機械的に再生成できる形」に分離するための契約。
数値の意味・単位は元ダッシュボードのJSと同じ（円、件、%は 0–100 の数値、CPAは円）。

## 入力（zensha/raw/*.md）
Google Drive MCP `read_file_content` が返す fileContent（Markdown表。タブ名は含まれず、空行で区切られた表ブロックの列）。
- `raw/keiei.md`  … スプレッドシート「FY26_経営モニタリング」 id `1oIz45k-Qkr8AY3v4zBQAiYHDxvg21nvVPSP5xX5q9pE`
  - ブロック2: 売上マトリクス（財務会計/管理会計 × 合計,_LKS,_MNY,_プロデ,_法人,_グロスタ × 2026-04..2027-03）。
    「期初計画」表 → 「実績」表（実績確定月まで）→ 「Ａヨミ」表 の順。Aヨミ表の _グロスタ 行は1列右にズレている（先頭 ¥7,851,000 は2026/3の値）。
  - ブロック3: 単月確認用（今月の 期初計画/実績/Aヨミ/Bヨミ をサービス別）。
  - ブロック4: 「実績確定月→ 2026/8」を持つ通期着地ワイド表。
  - ブロック15: 拠点KPIの月目標（32,154 / 26,500,000 を含む）。
- `raw/marke.md`  … 「26年9月_マーケジスイ進捗管理表」 id `1a6Aud7H-31gs64lDv2Iyww6DnS98wJcuoOssx6nGhJs`（月ごとにファイルが変わる。翌月は「26年10月_…」）
  - ブロック1: オンライン日次表（日付行 2026/9/1…、行「成約数 | 決済日起点」が日次成約）。
  - ブロック2: KPIサマリ（当月目標/昨日までの実績/Aヨミ/Bヨミ。全体成約数/オンライン成約数/拠点成約数/オンライン各KPI/拠点各KPI）。
  - ブロック8: オンライン月次（期初目標 7,612 等）。
- `raw/kyoten.md` … 「FY26_拠点モニタリング」 id `11pv1YWcxOvJwJjhsNFZa9zGoy0GCn3bauNtSymMppcg`
  - ブロック3: 9月目標（広告費 26,500,000 / ALL申込 824 / 参加率 48.40% / 成約率 / 成約数、拠点別CPA 9月目標・8月実績）。
  - 日次集計（梅田・福岡・横浜の最終成約の日次）はブロック番号を extract.py 側で「ヘッダ内容」から特定する（番号固定は禁止）。
ブロック番号は目安。extract.py は必ず「表の見出し文字列」で表を特定し、番号ズレに耐えること。

## 出力（zensha/data/latest.json）
```jsonc
{
  "contract_version": "1",
  "generated_at": "2026-09-18T09:35:00+09:00",   // 生成時刻(JST)
  "basis_date":   "2026-09-18",                   // データ取得日(JST今日)
  "data_through": "2026-09-17",                   // 実績が入っている最終日(= basis_date の前日)
  "target_month": "2026-09",
  "days_in_month": 30, "elapsed_days": 17, "remaining_days": 13,   // elapsed = data_through の日, remaining = days_in_month - elapsed
  "fy": { "label": "FY26", "months": ["2026-04","2026-05",…,"2027-03"] },
  "actual_until_index": 5,     // 実績確定月の数。keiei「実績確定月→ 2026/8」→ 4,5,6,7,8 の5ヶ月 → 5
  "revenue": {
    "fin":  { "total": {"plan":[12], "act":[12]}, "lks": {...}, "mny": {...}, "pro": {...}, "hjn": {...}, "grs": {...} },
    "mgmt": { 同上 }
  },
  // act[i] = i < actual_until_index ? 実績 : Aヨミ。null不可。Aヨミ欠損月は 直前月のAヨミ → 実績 → 期初計画 → 0 の順でフォールバックし revenue_corrections に記録
  //（例: Aヨミ表の _グロスタ 行ズレ補正で 2027-03 が欠けるため直前月 Aヨミ を採用）。実績の出所は「売上マトリクス」ブロックを正とし、他表（実績確定月→ ワイド表）との食い違いは extract_log に記録する。
  // total.plan = シートの合計行、total.act = 5サービスの act の和（財務・管理とも。管理会計の合計行はグロスタを含まないため）。
  // 管理会計・財務会計とも Aヨミ表の _グロスタ 行は1列左に戻してから使う。
  "revenue_corrections": [ "…適用した補正を日本語1行ずつ…" ],
  "month_summary": {           // keiei ブロック3（単月確認用）今月分。fin/mgmt × 6キー。シートの値をそのまま格納（補正しない）。表示には使わず検算用（単月表示は revenue[target_month] を使う）
    "fin":  { "total": {"plan":414385176, "act":354487394, "yomiA":406123321, "yomiB":417009520}, "lks": {...}, … },
    "mgmt": { … }
  },
  "lks": {
    "targets": { "online": 868, "kyoten": 148, "source": "keiei の目標表（オンライン: 売上目標/申込数目標/成約数目標 表の当月行、拠点: 拠点ALL 目標表の当月列）。marke KPIサマリの当月目標(995/147)ではなく keiei を正とする（原本注記どおり）" },
    "online": {
      "yomi": 896,           // KPIサマリ オンライン成約数 全体 最終成約数 Aヨミ
      "act":  477,           // 同 昨日までの実績
      "daily": { "dates": ["9/1","9/2",…], "dow": ["火","水",…], "v": [29,25,…] },  // marke ブロック1「成約数 決済日起点」。data_through まで。欠損日は null（0 と区別）
      "step": [               // 元JSの STEP.lksOn と同じ形。p=月計画, y=Aヨミ, a=昨日まで実績
        {"n":"申込","p":7612,"y":7827,"a":4477},
        {"n":"予約","p":7599,"y":7887,"a":5085},
        {"n":"参加","p":3135,"y":3360,"a":1797},
        {"n":"成約","p":868,"y":896,"a":477,"key":true},
        {"n":"参加率","p":41.3,"y":42.6,"a":35.3,"unit":"%","sep":true},
        {"n":"成約率","p":27.7,"y":26.7,"a":26.6,"unit":"%"},
        {"n":"CPA","p":23022,"y":22379,"a":22400,"unit":"¥","lowerBetter":true}
      ],
      "cost": { "plan": 175244808, "act": 100284050, "yomi": 175154000 },  // 各値 null 可（シートに無い場合）
      "step_note": "オンラインの月計画(p)は出所が混在: …",  // 任意。段階テーブルの下に出す注記（p/y/a の出所や導出の断り書き）
      "daily.note": "系列は「成約数（決済日起点）」"        // 任意（daily の中に "note" として持つ）。日次グラフ下の系列説明
    },
    "kyoten": {
      "yomi": 112, "act": 49,
      "daily": { "dates": [...], "dow": [...], "v": [...] },   // 日次集計（梅田・福岡・横浜）の最終成約の合算
      "step": [ 申込, 参加, 成約(key), 参加率(sep), 成約率, CPA ],  // 元JS STEP.lksKp と同じ並び（予約なし）
      "cost": { "plan": 26500000, "act": 13629656, "yomi": 26484640 },
      "sites": [ {"name":"梅田","cpa_prev":42730,"cpa_target":32000,"as_of_month":"2026-09"}, {"name":"福岡",…}, {"name":"横浜",…} ],  // kyoten ブロック3「CPA 9月目標/8月実績」。as_of_month（任意）= 読んだ表の目標月。当月の表が無く他月で代用したときは target_month と異なる（画面注記用）
      "excluded_sites": ["名古屋"],
      "step_note": null,   // 任意（online と同じ。無ければ注記を出さない）
      "daily": { "…": "…", "note": "系列は 日次集計_（梅田・福岡・横浜）の最終成約" }   // note は任意
    }
  },
  "sources": [ {"name":"FY26_経営モニタリング","label":"全社（単月確認用・通期着地見通し）","url":"https://docs.google.com/spreadsheets/d/1oIz45k-…/edit"}, {…}, {…} ],  // label は任意（画面のリンク文言。無ければ「元データ：{name}」）。
  // marke は月ごとにファイルが変わるため name は target_month から組み立てる。ID を raw/marke.meta.json から確認できないときは stale_id:true（任意）を立て、label に「（リンク先は前月分の可能性）」を付ける
  "extract_log": [ "どの表をどう特定したか、フォールバックしたか（人が読む用）" ]
}
```

### 任意フィールド（画面の注記に使う。無ければテンプレート側の既定文）
- `lks.online.step_note` / `lks.kyoten.step_note` … 文字列 or null。段階テーブルの直下に出す注記。
  計画値(p)の出所が混在する／導出値である等の断り書きを入れる。HTML タグは書かない（画面側でエスケープする）。
- `lks.online.daily.note` / `lks.kyoten.daily.note` … 文字列 or null。日次グラフ下の系列説明。
  無い場合、画面は「系列は「成約数（決済日起点）」」／「系列は 日次集計_（拠点名…）の最終成約」を既定で出す。
- `sources[].label` … 文字列 or 省略。リンク文言に使う（画面側で「元データ：」を前置するので、label に含めない）。
- これらは全て latest.json 由来の外部入力として扱い、画面側で HTML エスケープする。URL は http(s) のみ許可。

## 不変条件（extract.py が検査し、NGなら非0終了）
1. revenue の各系列は長さ12、全要素が数値（NaN/null禁止）。
2. fin/mgmt とも `total.act[i] == Σ services act[i]`（丸め誤差1円以内）。
3. 期初計画（plan）は原本と一致（extract.py 内に原本 REV_ALL の plan を **fy.label でキーして**保持し、不一致なら非0終了。未登録の年度＝基準値が無い年度では検査をスキップし extract_log に警告を残す＝**非0終了しない**）: 例 fin.total.plan[0]==382595924、fin.lks.plan[0]==339439531、mgmt.total.plan[0]==385856895、fin.grs.plan[0]==20900000（原本 REV_ALL と全12ヶ月一致を要求）。
4. 実績確定月までの act は **0 以上**の数値で、売上マトリクスの実績表の該当セルと一致（extract.py が raw に対して検査）。`¥0` はその月に売上が立たなかった正当な実績なので**非0終了にせず extract_log に記録**する（例: 財務会計 _グロスタ 2026-09 は ¥0）。負の値、または**5サービス全てが 0** の月だけ非0終了とする。原本(2026-09-08)との ±0.5% 比較は test_extract.py の回帰とし、逸脱セルは「raw の該当セルと一致」を assert する。実績確定月→ ワイド表と食い違う月は extract_log に列挙する。
5. `actual_until_index` は 1..12、`target_month` は fy.months に含まれる。
6. lks.online.daily.v の長さ == elapsed_days（data_through まで）。kyoten も同じ。要素は数値または null（欠損日）。null は extract_log に記録。
7. step の p/y/a は全て数値。key 行がちょうど1つ。
8. basis_date が JST の今日と一致（古い raw を誤って使わない）。

## 手で持つ値（zensha/data/manual.json。build.py が latest.json と合成）
- `decisions`: 経営判断が必要な事項（期限/見出し/本文/決定者）。
- `field_notes`: 「現場で対応中（報告のみ）」の文。
- `provisional`: SHEmoney / PRO の成約KPI（元JSの LANE/STEP/DAILY/CAUSE の mny/pro をそのまま格納。表示は「仮」バッジ付き）。
- `notes`: 「この画面の前提と、まだ決まっていないこと」の箇条書き（決定済み/Phase タグ付き）。
- `updated_at`: manual.json を人が更新した日。

## 改訂履歴
- v1.4 (2026-09-18): 不変条件4 を「0 以上（¥0 は正当な実績。負値と全サービス0のみ非0終了）」に緩和、不変条件3 の基準値を fy.label でキーし未登録年度はスキップ（警告）、sites[].as_of_month と sources[].stale_id を任意フィールドとして追加。
- v1.5 (2026-09-18): `lks.*.step_note` と `lks.*.daily.note` を任意フィールドとして定義（段階テーブル下の注記・日次グラフの系列説明）、`sources[].label` は「元データ：」を含めない純ラベルであることを明記、latest 由来の文字列は画面側で HTML エスケープ・URL は http(s) のみ、`lks.targets.online` / `lks.targets.kyoten` は正の数（build.py も検査）。
- v1.3 (2026-09-18): 不変条件3を extract.py 内で検査、不変条件4を「raw との一致＋ワイド表との差異ログ」に変更、グロスタ補正の month_summary 突合、marke ソース名は target_month から組み立て（食い違いは警告）、lks.online.step の p は 成約=keiei(868) / 申込・予約・参加・参加率・CPA=marke / 成約率=成約p÷参加p（導出、extract_log と画面注記に明記）。
- v1.2 (2026-09-18): sources[].label 任意、cost の各値 null 可、month_summary は検算用 を明記。
- v1.1 (2026-09-18): daily.v の null 許容、Aヨミ欠損月のフォールバック順（直前月Aヨミ）、targets の出所（keiei を正）、実績の出所（売上マトリクスを正）、cost.act 例値修正、month_summary は無補正 を明記。
