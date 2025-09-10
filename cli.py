## venvを有効化してから
# export FLASK_APP=app.py        # Windows: set FLASK_APP=app.py
# flask init-db

# cli.py
import click

def init_app(app):
    @app.cli.command("init-db")
    def init_db_cmd():
        """DBスキーマと初期データを作成"""
        # 遅延importで循環参照を回避
        from app import (
            init_db,
            init_user_db,
            init_child_item_db,
            init_checkout_history_db,
            init_item_application_db,
            init_application_history_db,
            init_inventory_check_db,
        )
        init_db()
        init_user_db()
        init_child_item_db()
        init_checkout_history_db()
        init_item_application_db()
        init_application_history_db()
        init_inventory_check_db()
        click.echo("Initialized the database.")
