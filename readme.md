# Python/Flask製 Excelライク在庫管理CRUDアプリ

このプロジェクトは、**Python/Flask + SQLite**を用いた、  
「**Excelライクな表操作体験を重視した多項目CRUD管理アプリ**」のサンプルです。

---

■ プロジェクト構成

crud_app/
├── app.py           # メインのFlaskアプリ（全ルーティング・DB処理、fields.jsonに基づく柔軟設計）
├── fields.json      # フィールド定義・設定（追加・必須・内部管理・表示可否を管理）
├── templates/
│   ├── index.html   # 一覧・編集・フィルタ・ページネーション（ExcelライクUI、fields.jsonのshow_in_indexに従う）
│   └── form.html    # 1アイテム追加用のExcelライク入力画面（fields.jsonのinternal=false項目のみ編集対象）
└── (その他: static/ migrations/ などは用途に応じて追加)

---

■ 特徴・依頼事項まとめ

- **フィールド定義をfields.jsonで柔軟に管理**
  - 各フィールドに「表示名」「内部項目名（key）」「必須か否か」「内部管理用か否か」「インデックス画面表示可否（show_in_index）」を設定可能
  - 例：`"internal": true` でフォーム非表示の内部管理用フィールドも設計可能
  - 例：`"show_in_index": true` で内部管理用でも一覧画面表示カラムに追加可能

- **Excel風テーブル編集・フィルタ**
  - 全項目を表でセル編集（fields.jsonのshow_in_index:trueのみ）
  - 一括編集・一括削除・一括保存に対応
  - 全項目でAND部分一致検索（フィルタ行）
  - フィルタ行のオンオフ切替や強調表示あり

- **追加画面もExcel風1件入力**
  - internal:false項目のみユーザー入力・必須バリデーション対応
  - 必須フィールドはfields.jsonで柔軟に設定

- **DBスキーマもfields.jsonと連動**
  - fields.jsonを変更した場合、DBファイルの再作成（または手動ALTER）が必要
  - 例：`status`のような新フィールド追加もfields.jsonの編集だけでOK

- **シェルスクリプト（show_project.sh）でファイル構成・内容を一括取得可**
  - treeコマンド・fileコマンド必須（未導入の場合は`sudo apt install tree file`等）

---

■ 注意事項・運用メモ

- DB（SQLiteファイル）はfields.jsonのスキーマ変更時に削除・再作成が必要
- セキュリティやパフォーマンス要件は簡易実装です。必要に応じて拡張を！
- フィールド増減や必須/表示/管理フラグの更新はfields.jsonを書き換えるだけ

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

■ 補足

- カスタマイズやフィールド構成の変更、運用履歴はこのREADMEに追記推奨
- ChatGPT等でファイル構造/内容を再認識させたい場合はREADME＋show_project.sh出力を貼ると便利

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
