# cli.py
import click
from db_schema import init_db as init_schema, seed_minimal, get_version, upgrade
from services import get_db

def init_app(app):
    @app.cli.command("init-db")
    def init_db_cmd():
        """スキーマ作成＋最低限のシード（冪等）"""
        init_schema()
        seed_minimal()
        click.echo(f"Initialized database (schema v{get_version()}).")

    @app.cli.command("seed-admin")
    @click.option("--username")
    @click.option("--password")
    @click.option("--email")
    @click.option("--department")
    @click.option("--realname")
    def seed_admin_cmd(username, password, email, department, realname):
        """管理者ユーザー/ロールを再確認（必要なら作成）。オプションで上書き可。"""
        import os
        if username:   os.environ["ADMIN_USERNAME"] = username
        if password:   os.environ["ADMIN_PASSWORD"] = password
        if email:      os.environ["ADMIN_EMAIL"] = email
        if department: os.environ["ADMIN_DEPARTMENT"] = department
        if realname:   os.environ["ADMIN_REALNAME"] = realname
        seed_minimal()
        click.echo("Ensured roles and admin user.")

    @app.cli.command("db-version")
    def db_version_cmd():
        click.echo(f"Schema version: {get_version()}")

    @app.cli.command("db-upgrade")
    def db_upgrade_cmd():
        upgrade()
        click.echo(f"Upgraded to schema version: {get_version()}")

    @app.cli.command("fk-check")
    def fk_check_cmd():
        """PRAGMA foreign_key_check の結果を表示（空ならOK）"""
        with get_db() as db:
            rows = db.execute("PRAGMA foreign_key_check").fetchall()
            if not rows:
                click.echo("OK: no foreign key violations.")
            else:
                for r in rows:
                    # r = (table, rowid, parent, fkid) 形式
                    table = r["table"] if isinstance(r, dict) else r[0]
                    rowid = r["rowid"] if isinstance(r, dict) else r[1]
                    parent = r["parent"] if isinstance(r, dict) else r[2]
                    click.echo(f"NG: table={table} rowid={rowid} parent={parent}")

    @app.cli.command("reset-db")
    @click.confirmation_option(prompt="This will DROP ALL TABLES. Continue?")
    def reset_db_cmd():
        """開発用：DBを初期化し直す（本番では禁止推奨）"""
        with get_db() as db:
            db.executescript("""
                PRAGMA foreign_keys=OFF;
                DROP TABLE IF EXISTS user_roles;
                DROP TABLE IF EXISTS roles;
                DROP TABLE IF EXISTS users;
                DROP TABLE IF EXISTS checkout_history;
                DROP TABLE IF EXISTS child_item;
                DROP TABLE IF EXISTS item_application;
                DROP TABLE IF EXISTS application_history;
                DROP TABLE IF EXISTS inventory_check;
                DROP TABLE IF EXISTS item;
                DROP TABLE IF EXISTS db_meta;
            """)
            db.commit()
        init_schema()
        seed_minimal()
        click.echo("Database reset and re-initialized.")
        click.echo(f"Schema version: {get_version()}")

    @app.cli.command("drop-old")
    def drop_old_cmd():
        """マイグレーション残骸の *_old テーブルを一括削除"""
        with get_db() as db:
            rows = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_old'"
            ).fetchall()
            names = [r[0] if isinstance(r, tuple) else r["name"] for r in rows]
            for n in names:
                db.execute(f"DROP TABLE IF EXISTS {n}")
            db.commit()
        click.echo("Dropped: " + (", ".join(names) if names else "(none)"))