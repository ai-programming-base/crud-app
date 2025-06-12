# Python/Flask製 Excelライク在庫管理CRUDアプリ

このプロジェクトは、**Python/Flask + SQLite**を用いた、  
「**Excelライクな表操作体験を重視した多項目CRUD管理アプリ**」のサンプルです。

---

■ プロジェクト構成

crud_app/  
├── app.py           # メインのFlaskアプリ（全ルーティング・DB処理、fields.jsonに基づく柔軟設計）  
├── fields.json      # フィールド定義・設定（追加・必須・内部管理・表示可否を管理）  
├── templates/  
│   ├── index.html        # 一覧・編集・フィルタ・ページネーション・入庫申請・承認画面リンク（fields.jsonのshow_in_indexに従う）  
│   ├── form.html         # 1アイテム追加用のExcelライク入力画面（fields.jsonのinternal=false項目のみ編集対象）  
│   ├── apply_form.html   # 入庫申請画面（選択アイテムの申請と数量チェック）  
│   └── approval.html     # 承認画面（申請中アイテムの一括承認/差し戻し）  
└── (その他: static/ migrations/ などは用途に応じて追加)  

---

■ 機能・特徴まとめ（2025/06/08更新）

- **fields.jsonでフィールド定義を柔軟管理**
  - 「表示名」「内部項目名（key）」「必須か否か」「内部管理用か否か」「インデックス画面表示可否（show_in_index）」を設定
  - 例：内部管理用でも`show_in_index:true`で一覧表示カラム可

- **Excel風テーブル編集・フィルタ**
  - 全項目を表でセル編集（fields.jsonのshow_in_index:trueのみ）
  - 一括編集・一括削除・一括保存に対応
  - AND部分一致検索（フィルタ行、オンオフ可）

- **追加画面（form.html）**
  - internal:false項目のみユーザー入力・必須バリデーション対応
  - 「追加」「追加して次を入力」ボタンあり（後者は同じ内容を再入力フォームへ表示）

- **Statusによる状態管理**
  - 新規追加時：Statusを「入庫前」に自動セット
  - 入庫申請時：「入庫申請中」に自動変更
  - 承認時：「入庫」
  - 差し戻し時：「入庫差し戻し」

- **入庫申請フロー**
  - 一覧から複数選択し「入庫申請」ボタン→apply_form.html
  - 申請画面で選択アイテム・数量チェック（全て必須）・申請ボタン
  - 申請ボタンでStatusを「入庫申請中」、メール送信はalertダイアログで代用

- **承認フロー**
  - index.htmlに「承認画面へ」ボタン
  - approval.htmlではStatusに「申請中」を含むアイテムを一覧表示
  - 左端チェックボックスで複数選択可、下部に差し戻しコメント欄
  - 「承認」「差し戻し」ボタン（いずれもチェック必須）
      - 承認：Statusを「入庫」にし、メール送信はダイアログで代用
      - 差し戻し：Statusを「入庫差し戻し」、コメント必須、メール送信はダイアログで代用

- **UI/UX強化**
  - 一覧/編集/申請/承認の各画面でバリデーションを強化
  - 必ずチェックや入力が必要な場合はJS/サーバー両側で防止

- **DBスキーマもfields.jsonと連動**
  - fields.jsonを変更した場合、DBファイルの再作成（または手動ALTER）が必要

- **シェルスクリプト（show_project.sh）でファイル構成・内容を一括取得可**
  - treeコマンド・fileコマンド必須（未導入の場合は`sudo apt install tree file`等）

---

■ 注意事項・運用メモ

- DB（SQLiteファイル）はfields.jsonのスキーマ変更時に削除・再作成が必要
- セキュリティやパフォーマンス要件は簡易実装です。必要に応じて拡張を！
- フィールド増減や必須/表示/管理/状態フラグの更新はfields.jsonを書き換えるだけ
- メール送信部分は実装されていません（全てalert等のダイアログ表示で代用）

---

■ セットアップ＆使い方

1. 依存パッケージ導入  
   pip install flask

2. アプリ起動  
   cd crud_app  
   python app.py

3. （ファイル内容・構成確認用）  
   ./show_project.sh

---

■ fields.jsonサンプル

```json
[
  {"name": "品番",    "key": "field1",   "required": true,  "internal": false, "show_in_index": true},
  {"name": "品名",    "key": "field2",   "required": true,  "internal": false, "show_in_index": true},
  {"name": "在庫数",  "key": "field3",   "required": false, "internal": false, "show_in_index": true},
  {"name": "ロケ",    "key": "field4",   "required": false, "internal": false, "show_in_index": true},
  {"name": "棚番号",  "key": "field5",   "required": false, "internal": false, "show_in_index": true},
  {"name": "カテゴリ","key": "field6",  "required": false, "internal": false, "show_in_index": true},
  {"name": "備考1",   "key": "field7",   "required": false, "internal": false, "show_in_index": false},
  {"name": "備考2",   "key": "field8",   "required": false, "internal": false, "show_in_index": false},
  {"name": "登録者",  "key": "field9",   "required": false, "internal": false, "show_in_index": false},
  {"name": "登録日",  "key": "field10",  "required": false, "internal": false, "show_in_index": false},
  {"name": "状態",    "key": "status",   "required": false, "internal": true,  "show_in_index": true}
]
