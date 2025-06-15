Python/Flask製 Excelライク在庫管理CRUDアプリ（2025/06/15更新）

【概要】
  本プロジェクトは Python/Flask + SQLite 製の
  Excelライクな表操作・複数ロール・入庫/持ち出し申請・承認・履歴管理が可能な在庫CRUDアプリのサンプルです。

【プロジェクト構成】

crud_app/
├── app.py               ... メインFlaskアプリ（全ルーティング・DB処理、fields.jsonに基づく柔軟設計）
├── fields.json          ... フィールド定義・設定（必須/内部/表示可否管理）
├── readme.md            ... このファイル
├── メモ_インポート方法  ... DBインポート方法や注意点
└── templates/           ... HTMLテンプレート群
    ├── index.html           ... 一覧・編集・フィルタ・ページネーション・申請・承認画面リンク
    ├── form.html            ... 追加（ExcelライクUI）
    ├── apply_form.html      ... 入庫・持ち出し申請画面
    ├── approval.html        ... 承認・差し戻し画面
    ├── login.html           ... ログイン画面
    └── register.html        ... ユーザー登録画面

【主な機能・特徴】
- fields.json で「表示・必須・内部」等のカラム定義を柔軟に管理
- 一覧は Excel風テーブル。編集は直接セル編集（Tab/Enter/Shift+Tab/Shift+Enterで移動可能）
- フィルタ・ページネーション・一括編集・一括保存・複数選択削除
- ログイン・ユーザー登録・複数ロール（manager/owner/general）対応
- 「起票（新規追加）」ボタンでデータ追加。入力時もセル単位でTab/Enter移動対応
- 入庫申請フォーム（複数選択可）、持ち出し申請も同時に可能
  - 持ち出し申請ON時は、各サンプル数分だけ枝番ごとに所有者入力欄（デフォルト申請ユーザー名、Excelライク編集可）が表示
- 申請後は申請内容を画面下部に一覧表示
- 申請・承認フロー管理：
  - 申請・承認操作ごとにapplication_historyテーブルへ履歴登録（申請者・承認者・コメント・時刻など全記録）
  - 承認時、申請内容に応じて「item.status」「child_item.status」も自動更新
  - 承認画面には「自分が承認者となっている案件のみ」表示し、一括承認・差し戻し可能

【DBスキーマ要点】
- item
    id INTEGER PRIMARY KEY AUTOINCREMENT
    ...（fields.jsonに準拠）
- child_item
    id INTEGER PRIMARY KEY AUTOINCREMENT
    item_id INTEGER NOT NULL
    branch_no INTEGER NOT NULL
    owner TEXT NOT NULL
    status TEXT NOT NULL
    checkout_start_date TEXT
    checkout_end_date TEXT
    UNIQUE(item_id, branch_no)   ← 複合キーで重複禁止＆申請時にON CONFLICTで上書き
- users / roles / user_roles
    ...（ユーザー・ロール管理）
- application_history
    id INTEGER PRIMARY KEY AUTOINCREMENT
    item_id INTEGER NOT NULL
    applicant TEXT NOT NULL
    application_content TEXT NOT NULL     ← 入庫申請／持ち出し申請
    applicant_comment TEXT
    application_datetime TEXT             ← 申請日時
    approver TEXT NOT NULL
    approver_comment TEXT
    approval_datetime TEXT                ← 承認・差し戻し日時
    status TEXT NOT NULL                  ← 申請中／承認／差し戻し

【UI改善・操作感】
- 一覧・入力フォームとも「Tab/Enter/Shift+Tab/Shift+Enter」対応でExcel風操作性
    - 一覧…Tabは右、Enterは下、Shift+Tab/Shift+Enterで左・上へ移動
    - 入力画面…Tab/Enterで右、Shift+Tab/Shift+Enterで左
- 必須項目はJSでバリデーション
- セル編集後はhidden inputにも値が自動反映
- 一覧のボタンは「起票（追加）」が一番左、以降「入庫申請」「編集内容一括保存」「承認」「選択削除」

【運用上の注意】
- fields.json変更時は必要に応じてDBを再作成
- インポート時は「ID」明示指定も可能（既存IDと重複不可、詳細はメモ_インポート方法参照）
- child_itemはitem_id, branch_noの組み合わせが重複不可。申請時、すでに存在する場合は内容上書き
- セキュリティやメール通知はダイアログで代用。実運用時は適宜拡張推奨

【セットアップ手順】
1. 依存パッケージ導入
   pip install flask
2. アプリ起動
   cd crud_app
   python app.py
3. 管理用テーブル・ユーザ・child_itemなどは初回起動時に自動作成されます

【補足】
- テンプレート・画面のUI/UXカスタマイズはcss, htmlの書き換えで簡単に拡張可能です
- 子アイテムや履歴管理の設計を他用途にも流用可能
- このReadmeは最新版の仕様・実装例を元に記述。都度アップデート推奨

【更新履歴】
- 2025/06/12: 持ち出し申請・所有者管理対応
- 2025/06/13: 申請/承認履歴テーブル・承認フロー強化
- 2025/06/14: child_item複合キー対応・ON CONFLICT実装
- 2025/06/15: 一覧/入力のセル移動操作改善（Tab/Enter/上下左右Excel風移動）
