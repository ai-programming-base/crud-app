import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, render_template
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json
from datetime import datetime, timedelta, timezone

import logging
from logging.handlers import RotatingFileHandler
import sys

from auth import authenticate
from send_mail import send_mail

# app.py（先頭の import 群の下あたり）
from services import (
    logger,
    DATABASE, FIELDS_PATH, FIELDS, USER_FIELDS, INDEX_FIELDS, FIELD_KEYS,
    SELECT_FIELD_PATH, load_select_fields, save_select_fields,
    get_db,
    LOCK_TTL_MIN, _now, _is_lock_expired, acquire_locks, release_locks, _cleanup_expired_locks,
    login_required, roles_required,
    get_managers_by_department, get_proper_users, get_partner_users,
    get_user_profile, get_user_profiles,
)

app = Flask(__name__)
app.secret_key = "any_secret"

from blueprints.index_bp import index_bp
app.register_blueprint(index_bp)

from blueprints.checkout_bp import checkout_bp
app.register_blueprint(checkout_bp)

from blueprints.approval_bp import approval_bp
app.register_blueprint(approval_bp)

from blueprints.child_items_bp import child_items_bp
app.register_blueprint(child_items_bp)

from blueprints.entry_request_bp import entry_request_bp
app.register_blueprint(entry_request_bp)

from blueprints.return_request_bp import return_request_bp
app.register_blueprint(return_request_bp)

from blueprints.dispose_transfer_request_bp import dispose_transfer_request_bp
app.register_blueprint(dispose_transfer_request_bp)

from blueprints.bulk_manager_change_bp import bulk_manager_change_bp
app.register_blueprint(bulk_manager_change_bp)

from blueprints.change_owner_bp import change_owner_bp
app.register_blueprint(change_owner_bp)

from blueprints.inventory_bp import inventory_bp
app.register_blueprint(inventory_bp)

from blueprints.bulk_edit_bp import bulk_edit_bp
app.register_blueprint(bulk_edit_bp)

from blueprints.users_bp import users_bp
app.register_blueprint(users_bp)

from blueprints.auth_bp import auth_bp
app.register_blueprint(auth_bp)

from blueprints.raise_request_bp import raise_request_bp
app.register_blueprint(raise_request_bp)

from flask import url_for as _flask_url_for
@app.context_processor
def _urlfor_compat():
    def url_for_compat(endpoint, **values):
        # 互換マッピング：旧 endpoint -> 新 endpoint
        mapping = {
            'index': 'index_bp.index',
            'checkout_request': 'checkout_bp.checkout_request',
            'approval': 'approval_bp.approval',
            'child_items': 'child_items_bp.child_items',
            'entry_request': 'entry_request_bp.entry_request',
            'return_request': 'return_request_bp.return_request',
            'dispose_transfer_request': 'dispose_transfer_request_bp.dispose_transfer_request',
            'bulk_manager_change': 'bulk_manager_change_bp.bulk_manager_change',
            'change_owner': 'change_owner_bp.change_owner',
            'inventory_list': 'inventory_bp.inventory_list',
            'inventory_check': 'inventory_bp.inventory_check',
            'inventory_history': 'inventory_bp.inventory_history',
            'bulk_edit': 'bulk_edit_bp.bulk_edit',
            'bulk_edit_commit': 'bulk_edit_bp.bulk_edit_commit',
            'bulk_edit_cancel': 'bulk_edit_bp.bulk_edit_cancel',
            'unlock_my_locks': 'bulk_edit_bp.unlock_my_locks',
            'users_list': 'users_bp.users_list',
            'register': 'users_bp.register',
            'edit_user': 'users_bp.edit_user',
            'login': 'auth_bp.login',
            'logout': 'auth_bp.logout',
            'raise_request': 'raise_request_bp.raise_request',
            'delete_selected': 'raise_request_bp.delete_selected',
        }
        endpoint = mapping.get(endpoint, endpoint)
        return _flask_url_for(endpoint, **values)
    return dict(url_for=url_for_compat)


# グローバルロガーを作成
logger = logging.getLogger("myapp")  # 任意の名前

# フォーマッター
formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s [%(filename)s:%(lineno)d]')

# ファイル用ハンドラ（INFO以上）
file_handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=3)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

# stdout用ハンドラ（WARNING以上）
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.DEBUG)
stdout_handler.setFormatter(formatter)

# 既存のハンドラをリセット（多重出力防止）
logger.handlers = []
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(stdout_handler)
logger.propagate = False  # これでroot loggerへの伝播を防止


def init_db():
    with get_db() as db:
        db.execute(f'''
            CREATE TABLE IF NOT EXISTS item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {",".join([f"{f['key']} TEXT" for f in FIELDS])}
            )
        ''')

def init_user_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT,
                email TEXT,
                department TEXT,
                realname TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER,
                role_id INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(role_id) REFERENCES roles(id),
                PRIMARY KEY(user_id, role_id)
            )
        """)
        # ロール登録
        for role in ["admin", "manager", "proper", "partner"]:
            db.execute("INSERT OR IGNORE INTO roles (name) VALUES (?)", (role,))
        # パスワードをハッシュ化してadminユーザー登録
        hashed = generate_password_hash("adminpass")
        db.execute("""
            INSERT OR IGNORE INTO users (username, password, email, department, realname)
            VALUES (?, ?, ?, ?, ?)
        """, ("admin", hashed, "admin@example.com", "管理部門", "管理者"))
        admin_id = db.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()['id']
        admin_role_id = db.execute("SELECT id FROM roles WHERE name = ?", ("admin",)).fetchone()['id']
        db.execute("""
            INSERT OR IGNORE INTO user_roles (user_id, role_id)
            VALUES (?, ?)
        """, (admin_id, admin_role_id))
        db.commit()

def init_child_item_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS child_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                branch_no INTEGER NOT NULL,
                owner TEXT NOT NULL,
                status TEXT NOT NULL,
                comment TEXT,
                transfer_dispose_date TEXT,
                UNIQUE(item_id, branch_no)
            )
        ''')
        db.commit()
        
def init_checkout_history_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS checkout_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                checkout_start_date TEXT NOT NULL,
                checkout_end_date TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES item(id)
            )
        ''')
        db.commit()

def init_item_application_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS item_application (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                new_values TEXT NOT NULL,
                applicant TEXT NOT NULL,
                applicant_comment TEXT,
                approver TEXT,
                status TEXT NOT NULL,
                application_datetime TEXT NOT NULL,
                approval_datetime TEXT,
                approver_comment TEXT,
                original_status TEXT
            )
        ''')
        db.commit()


def init_application_history_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS application_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                applicant TEXT NOT NULL,
                application_content TEXT,
                applicant_comment TEXT,
                application_datetime TEXT NOT NULL,
                approver TEXT,
                approver_comment TEXT,
                approval_datetime TEXT,
                status TEXT NOT NULL
            )
        ''')
        db.commit()

def init_inventory_check_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS inventory_check (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                checker TEXT NOT NULL,
                comment TEXT,
                FOREIGN KEY(item_id) REFERENCES item(id)
            )
        ''')
        db.commit()

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    g.user = None
    g.user_roles = []
    if user_id:
        db = get_db()
        g.user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        g.user_roles = [r['name'] for r in db.execute("""
            SELECT roles.name FROM roles
            JOIN user_roles ON roles.id = user_roles.role_id
            WHERE user_roles.user_id=?
        """, (user_id,))]


@app.route('/select_field_config', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager')
def select_field_config():
    # fields.jsonのユーザー項目のみ
    fields = [f for f in FIELDS if not f.get('internal')]
    # 現在の選択式設定をロード
    select_fields = load_select_fields()   # ←定義必要（例：JSONファイル読み込み）

    if request.method == 'POST':
        # 選択式フィールド情報の保存
        new_select_fields = {}
        for f in fields:
            key = f['key']
            # チェックボックス等で「選択式」指定されているか
            if request.form.get(f"use_{key}") == "1":
                options = request.form.get(f"options_{key}", "")
                # カンマ区切りで保存
                opts = [o.strip() for o in options.split(",") if o.strip()]
                if opts:
                    new_select_fields[key] = opts
        save_select_fields(new_select_fields)  # ←定義必要（例：JSONファイル書き込み）
        flash("設定を保存しました")
        return redirect(url_for('select_field_config'))

    return render_template("select_field_config.html", fields=fields, select_fields=select_fields)


@app.route('/menu')
@login_required
def menu():
    return render_template('menu.html')


@app.route('/my_applications')
@login_required
def my_applications():
    status = request.args.get('status', 'all')
    db = get_db()
    params = [g.user['username']]
    where = "applicant=?"
    if status == "approved":
        where += " AND status='承認'"
    elif status == "pending":
        where += " AND status!='承認'"
    apps = db.execute(f"""
        SELECT * FROM item_application
        WHERE {where}
        ORDER BY application_datetime DESC
    """, params).fetchall()

    # ★ department realname 形式（realname が空なら username を使う）
    user_rows = db.execute("""
        SELECT
            username,
            TRIM(
                COALESCE(NULLIF(department,''),'') || ' ' ||
                COALESCE(NULLIF(realname,''), username)
            ) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    return render_template(
        'my_applications.html',
        applications=apps,
        status=status,
        user_display=user_display,
    )

@app.template_filter('loadjson')
def loadjson_filter(s):
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


@app.route('/print_labels', methods=['GET', 'POST'])
@login_required
def print_labels():
    # 選択されたIDのリストを取得（例: POSTでもGETでもOKなように）
    if request.method == 'POST':
        ids = request.form.getlist('selected_ids')
    else:
        ids = request.args.get('ids', '').split(',')

    ids = [int(i) for i in ids if i.isdigit()]
    if not ids:
        flash("ラベル印刷するアイテムを選択してください。")
        return redirect(url_for('index_bp.index'))

    db = get_db()
    items = db.execute(
        f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(ids))})",
        ids
    ).fetchall()
    items = [dict(row) for row in items]
    items_sorted = sorted(items, key=lambda x: x['id'])

    # fields.json読み込み
    FIELDS_PATH = os.path.join(os.path.dirname(__file__), 'fields.json')
    with open(FIELDS_PATH, encoding='utf-8') as f:
        fields = json.load(f)

    return render_template(
        'print_labels.html',
        items=items_sorted,
        fields=fields
    )


if __name__ == '__main__':
    init_db()
    init_user_db()
    init_child_item_db()
    init_checkout_history_db()
    init_item_application_db()
    init_application_history_db()
    init_inventory_check_db()
    app.run(debug=True, host='0.0.0.0', port=8000)
