# Python/Flask製 Excelライク在庫管理CRUDアプリ

このプロジェクトは、**Python/Flask + SQLite**を用いた  
「**Excelライクな表操作体験を重視した多項目CRUD＋入庫・持ち出し申請管理アプリ**」のサンプルです。

---

■ プロジェクト構成

crud_app/  
├── app.py             # メインFlaskアプリ（全ルーティング・DB処理、fields.jsonに基づく柔軟設計）  
├── fields.json        # フィールド定義・設定（必須/内部/表示可否管理）  
├── templates/         # HTMLテンプレート（一覧・編集・申請・承認・認証系）  
│   ├── index.html        # 一覧・編集・フィルタ・ページネーション・申請・承認画面リンク  
│   ├── form.html         # 追加入力（ExcelライクUI）  
│   ├── apply_form.html   # 入庫・持ち出し申請画面  
│   ├── approval.html     # 承認・差し戻し画面  
│   ├── login.html        # ログイン画面  
│   └── register.html     # ユーザー登録画面  
├── メモ_インポート方法 # DB import方法や注意メモ  
└── readme.md           # 本ファイル（構成/仕様/運用/注意点）

---

■ 主な機能・特徴（2025/06/12更新）

- **fields.jsonでフィールド定義・表示/必須/管理/内部項目を柔軟に管理**
- **Excelライクな一覧・一括編集・一括保存・一括削除**
- **AND部分一致フィルタ・ページネーション**
- **追加画面（form.html）はinternal=falseのみ対象。必須バリデーションあり**
- **ユーザー管理/ログイン/複数ロール（manager, owner, general）対応**
- **申請→承認フロー＋差し戻し（コメント可）、各状態管理（statusカラム）**
- **インポート時ID指定対応（DB設計で自動採番両立、詳細はメモ参照）**

---

■ **持ち出し申請対応（2025/06/12追加分）**

- **入庫申請時「持ち出し申請も同時に行う」チェックをONにすると：**
    - 申請対象の各IDごとに、その「サンプル数」分だけ枝番行を持つ「所有者」表が画面上に動的生成
    - 枝番は1からサンプル数までインクリメント
    - 所有者名はデフォルトで申請者ユーザー名。セルはExcelライク編集可能
- **申請送信時：**
    - 各対象IDのstatusを「入庫持ち出し申請中」に自動セット（通常申請は「入庫申請中」）
    - 更に「持ち出し申請」を行った場合、**checkout_request**テーブルに下記を登録
        - item_id（アイテムID）
        - branch_no（枝番、1～サンプル数）
        - owner（入力された所有者名、デフォルトユーザー名）
        - status（申請直後は「持ち出し申請中」）
    - 所有者情報はhidden input+contenteditableテーブルでサブミットされる

---

■ DBスキーマ例

- **item**  
  - id INTEGER PRIMARY KEY AUTOINCREMENT  
  - …（fields.jsonに準拠したフィールド群）  
- **users / roles / user_roles**（ユーザー・ロール管理用）
- **checkout_request**（2025/06/12新設・持ち出し申請情報）  
  - id INTEGER PRIMARY KEY AUTOINCREMENT  
  - item_id INTEGER NOT NULL  
  - branch_no INTEGER NOT NULL  
  - owner TEXT NOT NULL  
  - status TEXT NOT NULL（申請時は「持ち出し申請中」）

---

■ 申請・承認フロー

1. **一覧から複数選択→「入庫申請」ボタン**
2. **申請フォームで数量チェック、必要項目入力、持ち出し申請も同時に可**
    - 持ち出し申請ON時は所有者入力表が表示される
3. **申請ボタンで各itemのstatus更新＋持ち出し申請ならcheckout_requestレコードを追加**
4. **承認画面で一括承認・差し戻し、コメント付可。差し戻しもstatus管理**

---

■ 運用・注意事項

- fields.json変更時はDBスキーマ再作成推奨
- インポート時IDの明示指定可（重複不可）
- メール送信はalert/ダイアログで代用（実装省略）
- セキュリティや排他制御は必要に応じて拡張してください

---

■ セットアップ＆使い方

1. **依存パッケージ導入**  
   `pip install flask`

2. **アプリ起動**  
   `cd crud_app`  
   `python app.py`

3. **プロジェクト構成・ファイル確認用**  
   `./show_project.sh`

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
