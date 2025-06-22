Python/Flask製 Excelライク在庫管理CRUDアプリ（2025/06/22更新）

【概要】
  本プロジェクトは Python/Flask + SQLite 製の
  Excelライクな表操作・複数ロール・入庫/持ち出し/返却申請・承認・履歴管理が可能な在庫CRUDアプリのサンプルです。

【プロジェクト構成】

``` text
crud_app/
├── app.py               ... メインFlaskアプリ（全ルーティング・DB処理、fields.jsonに基づく柔軟設計）  
├── fields.json          ... フィールド定義・設定（必須/内部/表示可否管理）  
├── メモ_インポート方法  ... DBインポート方法や注意点  
└── templates/           ... HTMLテンプレート群  
    ├── index.html           ... 一覧・編集・フィルタ・ページネーション・申請・承認画面リンク  
    ├── form.html            ... 追加（ExcelライクUI）  
    ├── apply_form.html      ... 入庫・持ち出し申請画面  
    ├── return_form.html     ... 持ち出し終了（返却）申請画面  
    ├── approval.html        ... 承認・差し戻し画面（申請種別ごとに内容詳細を動的表示）  
    ├── bulk_manager_change.html ... 管理者一括変更画面  
    ├── child_items.html     ... 小アイテム（子アイテム）表示  
    ├── login.html           ... ログイン画面  
    └── register.html        ... ユーザー登録画面  
``` 

【主な機能・特徴】
- fields.json で「表示・必須・内部」等のカラム定義を柔軟に管理
- 一覧は Excel風テーブル。編集は直接セル編集（Tab/Enter/Shift+Tab/Shift+Enterで移動可能）
- フィルタ・ページネーション・一括編集・一括保存・複数選択削除
- ログイン・ユーザー登録・複数ロール（manager/owner/general）対応
- 「起票（新規追加）」ボタンでデータ追加。入力時もセル単位でTab/Enter移動対応

【申請・承認フローと履歴管理】  
- 入庫申請、**持ち出し申請（同時可）**、**持ち出し終了（返却）申請**は**すべてitem_applicationテーブルを介して一元管理**
    - 申請内容はnew_values(JSON)として保存。itemテーブルは申請種別ごとに一時的なステータス変更のみ即時反映
    - 申請後は「申請中」扱いとなり、承認画面で**詳細内容（申請種別ごとに必要項目のみ）**を確認可能
- **承認・差し戻し**はapproval.htmlから一括・複数対応
    - **承認時の処理は申請種別ごとに自動で分岐**  
      - 入庫申請: item.statusを「入庫」へ
      - 持ち出し申請: item.status「持ち出し中」、child_itemテーブルを生成・更新
      - **持ち出し終了（返却）申請**: item.statusを「入庫」へ戻し、item.storage（保管場所）を申請値で更新、child_item.checkout_end_date（返却確認日）を申請値で更新、child_item.statusを「返却済」へ
- **履歴（application_history）はすべての申請/承認/差し戻しで自動登録**
    - 申請者/承認者/コメント/日付等、経路ごとに記録

【申請画面・承認画面の仕様】
- 申請画面（apply_form.html, return_form.html）は用途別（持ち出し終了申請画面もUIはそのまま）
- **承認画面（approval.html）は申請内容詳細を**  
    - **入庫申請/持ち出し申請は従来どおり**
    - **持ち出し終了申請は「返却確認日」「保管場所」「コメント」のみ動的に表示**
    - テンプレ内で申請内容種別（status）ごとに出し分け実装済

【DBスキーマ要点】
- item
    id INTEGER PRIMARY KEY AUTOINCREMENT
    ...（fields.jsonに準拠、storage/保管場所含む）
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
- item_application
    id INTEGER PRIMARY KEY AUTOINCREMENT
    item_id INTEGER
    new_values TEXT NOT NULL      ← 申請内容（申請種別ごとに可変/JSON形式）
    applicant TEXT NOT NULL
    applicant_comment TEXT
    approver TEXT
    status TEXT NOT NULL          ← 申請中／承認／差し戻し
    application_datetime TEXT NOT NULL
    approval_datetime TEXT
    approver_comment TEXT
- application_history
    id INTEGER PRIMARY KEY AUTOINCREMENT
    item_id INTEGER NOT NULL
    applicant TEXT NOT NULL
    application_content TEXT      ← 入庫申請／持ち出し申請／持ち出し終了申請
    applicant_comment TEXT
    application_datetime TEXT NOT NULL
    approver TEXT
    approver_comment TEXT
    approval_datetime TEXT
    status TEXT NOT NULL          ← 申請中／承認／差し戻し

【UI改善・操作感】
- 一覧・入力フォームとも「Tab/Enter/Shift+Tab/Shift+Enter」対応でExcel風操作性
    - 一覧…Tabは右、Enterは下、Shift+Tab/Shift+Enterで左・上へ移動
    - 入力画面…Tab/Enterで右、Shift+Tab/Shift+Enterで左
- 必須項目はJSでバリデーション
- セル編集後はhidden inputにも値が自動反映
- 一覧のボタンは「起票（追加）」が一番左、以降「入庫申請」「持ち出し終了申請」「編集内容一括保存」「承認」「選択削除」「管理者一括変更」「小アイテム表示」

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
- 申請内容に新項目を加えた場合もnew_values(JSON)に含めれば承認画面で内容詳細が動的に表示できます

【更新履歴】
- 2025/06/12: 持ち出し申請・所有者管理対応
- 2025/06/13: 申請/承認履歴テーブル・承認フロー強化
- 2025/06/14: child_item複合キー対応・ON CONFLICT実装
- 2025/06/15: 一覧/入力のセル移動操作改善（Tab/Enter/上下左右Excel風移動）
- 2025/06/22: **持ち出し終了申請をitem_application経由に統一・申請種別ごとの承認/反映ロジック/画面動的出力に完全対応**

