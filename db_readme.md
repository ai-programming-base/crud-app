# CLI コマンド一覧

## 1回だけ（開発/本番初期化）
$ export FLASK_APP=app.py
$ flask init-db
- テーブル作成（なければ）＋最低限のロールと管理者ユーザーを投入します。  
- 本番初期導入でも使えます。  

---

## 管理者ユーザーを作り直したいとき
$ flask seed-admin --username admin --password 'newpass' --email 'admin@example.com'
- 既存ロールを確認し、必要なら作成。  
- 指定した環境変数で管理者ユーザーを上書き登録します。  

---

## スキーマ版数を確認
$ flask db-version
- `db_meta` テーブルに記録された現在のスキーマバージョンを表示。  

---

## マイグレーション実行（将来用）
$ flask db-upgrade
- バージョンが古ければ upgrade() を実行し、スキーマを最新化。  
- v2→v3 では外部キー制約の是正や孤児データの掃除を行います。  

---

## 開発用：DBを完全リセット
$ flask reset-db
- **開発専用**。すべてのテーブルを DROP → init-db & seed-minimal を再実行。  
- 本番では利用禁止推奨。  

---

## 外部キー整合性チェック（開発/デバッグ用）
$ flask fk-check
- `PRAGMA foreign_key_check` の結果を出力。  
- 出力が空なら「外部キー制約違反なし」。  

---

## マイグレーション残骸の削除（万一の時用）
$ flask drop-old
- 途中失敗で残った `*_old` テーブルを一括削除。  
- `fk-check` で NG が出た場合のリカバリに使用。  
