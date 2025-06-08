# Python/Flask製 Excelライク在庫管理CRUDアプリ

このプロジェクトは、**Python/Flask + SQLite**を用いた、
「**Excelライクな表操作体験を重視した多項目CRUD管理アプリ**」のサンプルです。

---

■ プロジェクト構成

crud_app/
├── app.py           # メインのFlaskアプリ（全ルーティング・DB処理）
├── templates/
│   ├── index.html   # 一覧・編集・フィルタ・ページネーション（ExcelライクUI）
│   └── form.html    # 1アイテム追加用のExcelライク入力画面
└── (その他: static/ migrations/ などは用途に応じて追加)

---

■ 特徴・依頼事項まとめ

- 全項目をテーブルで表示・編集
  - 10項目（field1〜field10）すべてをExcelのように「セル編集」できるUI
  - 一括編集・一括削除・一括保存に対応
- フィルタもテーブル2行目でExcel風入力
  - 全項目でAND部分一致検索
  - フィルタ行のオンオフ切替や強調表示あり
- ページネーション対応
- 追加画面もExcel風1件入力
  - field1, field2は必須バリデーション
- DBスキーマ拡張や仕様変更にも再利用しやすい設計
- シェルスクリプト（show_project.sh）で常に最新ファイル構成・内容を一括取得可

---

■ 注意事項・運用メモ

- DB（SQLiteファイル）はスキーマ変更時に削除・再作成が必要な場合あり
- `show_project.sh`はtreeコマンド・fileコマンドが必要です（未導入の場合は`sudo apt install tree file`等）
- 大容量・バイナリファイルは自動的に内容表示を省略します
- セキュリティやパフォーマンス要件は簡易実装です。必要に応じて拡張を！

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

- 特徴的な依頼内容や追加カスタマイズ履歴があれば、このREADMEに追記していくと後工程や引継ぎがスムーズです
- ChatGPT等でファイル構造/内容を再認識させたい場合もこのREADMEの情報＋show_project.sh出力を貼るのが便利です。

---
