# 1回だけ（開発/本番初期化）
export FLASK_APP=app.py
flask init-db

# 追加で管理者だけ作り直したい時など
flask seed-admin --username admin --password 'newpass' --email 'admin@example.com'

# スキーマ版数を見る
flask db-version

# （将来）マイグレーションが追加されたら
flask db-upgrade

# 開発でDBをリセットしたい時
flask reset-db
