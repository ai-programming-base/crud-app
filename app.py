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
            # 将来BP化したらここに足していく:
            # 'login': 'auth_bp.login',
            # 'menu':  'menu_bp.menu',
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        auth_result = authenticate(username, password)
        if auth_result.get('result'):
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if user:
                # パスワードは一切触らず、他情報だけ更新
                db.execute("""
                    UPDATE users SET email=?, department=?, realname=? WHERE username=?
                """, (auth_result['email'], auth_result['department'], auth_result['realname'], username))
                db.commit()
                user_id = user['id']
            else:
                # 新規ユーザーの場合もpasswordは登録しない
                db.execute("""
                    INSERT INTO users (username, email, department, realname)
                    VALUES (?, ?, ?, ?)
                """, (username, auth_result['email'], auth_result['department'], auth_result['realname']))
                user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']
                db.commit()
            session['user_id'] = user_id
            return redirect(url_for('index_bp.index'))
        else:
            flash(auth_result.get('reason') or "ログインに失敗しました")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))


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


@app.route('/register', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def register():
    db = get_db()
    roles = db.execute("SELECT id, name FROM roles").fetchall()
    error = None

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']
        department = request.form['department']
        realname = request.form['realname']
        selected_roles = request.form.getlist('roles')

        # 入力バリデーション例
        if not username or not password or not email:
            error = 'ユーザー名、パスワード、メールは必須です'
        elif db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            error = 'そのユーザー名は既に使われています'
        else:
            # ユーザー追加
            db.execute(
                "INSERT INTO users (username, password, email, department, realname) VALUES (?, ?, ?, ?, ?)",
                (username, generate_password_hash(password), email, department, realname)
            )
            user_id = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()['id']

            # ロール追加
            for role_id in selected_roles:
                db.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    (user_id, role_id)
                )
            db.commit()
            flash('ユーザー登録が完了しました')
            return redirect(url_for('users_list'))

    return render_template('register.html', roles=roles)


@app.route('/users', methods=['GET'])
@login_required
@roles_required('admin', 'manager')
def users_list():
    db = get_db()
    q = request.args.get('q', '').strip()

    base_sql = """
        SELECT
            u.id, u.username, u.email, u.department, u.realname,
            COALESCE(GROUP_CONCAT(r.name, ', '), '') AS roles
        FROM users u
        LEFT JOIN user_roles ur ON ur.user_id = u.id
        LEFT JOIN roles r ON r.id = ur.role_id
    """
    params = []
    where = ""
    if q:
        where = """
            WHERE u.username LIKE ? OR u.email LIKE ?
               OR u.department LIKE ? OR u.realname LIKE ?
        """
        like = f"%{q}%"
        params = [like, like, like, like]

    group_order = " GROUP BY u.id ORDER BY u.username ASC"

    rows = db.execute(base_sql + where + group_order, params).fetchall()
    users = [dict(r) for r in rows]

    return render_template('users_list.html', users=users, q=q)


@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager')
def edit_user(user_id):
    db = get_db()
    # 編集対象ユーザー
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("対象ユーザーが見つかりません")
        return redirect(url_for('index_bp.index'))

    # 全ロール
    roles = db.execute("SELECT id, name FROM roles").fetchall()
    # そのユーザーの現在ロールID集合
    cur_role_rows = db.execute("SELECT role_id FROM user_roles WHERE user_id=?", (user_id,)).fetchall()
    current_role_ids = {str(r['role_id']) for r in cur_role_rows}

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')  # 空なら変更しない
        email = request.form.get('email', '').strip()
        department = request.form.get('department', '').strip()
        realname = request.form.get('realname', '').strip()
        selected_roles = request.form.getlist('roles')

        # 入力バリデーション
        if not username or not email:
            error = 'ユーザー名、メールは必須です'
        else:
            # 自分以外に同名がいないか
            u = db.execute("SELECT id FROM users WHERE username=? AND id<>?", (username, user_id)).fetchone()
            if u:
                error = 'そのユーザー名は既に使われています'

        if error:
            flash(error)
        else:
            # users更新（パスワードは空なら変更しない）
            if password:
                db.execute(
                    "UPDATE users SET username=?, password=?, email=?, department=?, realname=? WHERE id=?",
                    (username, generate_password_hash(password), email, department, realname, user_id)
                )
            else:
                db.execute(
                    "UPDATE users SET username=?, email=?, department=?, realname=? WHERE id=?",
                    (username, email, department, realname, user_id)
                )

            # ロール更新：一旦削除→再挿入
            db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
            for role_id in selected_roles:
                db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))

            db.commit()
            flash('ユーザー情報を更新しました')
            return redirect(url_for('edit_user', user_id=user_id))

    # GET または バリデーションNG時の再表示
    # 最新のユーザー情報を再取得してテンプレートに渡す
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    cur_role_rows = db.execute("SELECT role_id FROM user_roles WHERE user_id=?", (user_id,)).fetchall()
    current_role_ids = {str(r['role_id']) for r in cur_role_rows}

    return render_template('user_edit.html',
                           user=user,
                           roles=roles,
                           current_role_ids=current_role_ids)

@app.route('/menu')
@login_required
def menu():
    return render_template('menu.html')


@app.route('/raise_request', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def raise_request():
    SELECT_FIELDS_PATH = os.path.join(os.path.dirname(__file__), 'select_fields.json')
    if os.path.exists(SELECT_FIELDS_PATH):
        with open(SELECT_FIELDS_PATH, encoding='utf-8') as f:
            select_fields = json.load(f)
    else:
        select_fields = {}

    if request.method == 'POST':
        user_values = {}
        errors = []
        for f in USER_FIELDS:
            v = request.form.get(f['key'], '').strip()
            user_values[f['key']] = v
            if f.get('required') and not v:
                errors.append(f"{f['name']}（必須）を入力してください。")
            # int型バリデーション
            if f.get('type') == 'int' and v:
                if not v.isdigit() or int(v) < 1:
                    errors.append(f"{f['name']}は1以上の整数で入力してください。")
        internal_values = []
        for f in FIELDS:
            if f.get('internal', False):
                if f['key'] == 'status':
                    internal_values.append("入庫前")
                else:
                    internal_values.append("")
        # DB登録用リストは順序通り
        values = [user_values.get(f['key'], '') for f in USER_FIELDS] + internal_values

        if errors:
            for msg in errors:
                flash(msg)
            # 再描画時もdictで渡す
            return render_template('raise_request.html', fields=USER_FIELDS, values=user_values, select_fields=select_fields)

        db = get_db()
        db.execute(
            f'INSERT INTO item ({",".join(FIELD_KEYS)}) VALUES ({",".join(["?"]*len(FIELD_KEYS))})',
            values
        )
        db.commit()

        if 'add_and_next' in request.form:
            return render_template('raise_request.html', fields=USER_FIELDS, values=user_values, select_fields=select_fields, message="登録しました。同じ内容で新規入力できます。")
        else:
            if request.form.get('from_menu') or request.args.get('from_menu'):
                return redirect(url_for('menu'))
            else:
                return redirect(url_for('index_bp.index'))

    # --- GET時（コピーして起票サポート）---
    # パラメータでcopy_idが来たら、そのIDの値を初期値に使う
    copy_id = request.args.get("copy_id")
    values = {f['key']: "" for f in USER_FIELDS}
    if copy_id:
        db = get_db()
        item = db.execute("SELECT * FROM item WHERE id=?", (copy_id,)).fetchone()
        if item:
            item = dict(item)
            for f in USER_FIELDS:
                values[f['key']] = str(item.get(f['key'], '')) if item.get(f['key']) is not None else ""
    return render_template('raise_request.html', fields=USER_FIELDS, values=values, select_fields=select_fields)


@app.route('/delete_selected', methods=['POST'])
@login_required
@roles_required('admin', 'manager')
def delete_selected():
    ids = request.form.getlist('selected_ids')
    if ids:
        db = get_db()
        db.executemany('DELETE FROM item WHERE id=?', [(item_id,) for item_id in ids])
        db.commit()
    if request.form.get('from_menu') or request.args.get('from_menu'):
        return redirect(url_for('menu'))
    else:
        return redirect(url_for('index_bp.index'))


@app.route('/entry_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def entry_request():
    db = get_db()

    # properユーザーリスト
    proper_users = get_proper_users(db)  # [{'username':..., 'department':..., 'realname':...}, ...]
    proper_usernames = [u['username'] for u in proper_users]
    proper_users_json = [
        {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        } for u in proper_users
    ]

    # partnerユーザーリスト
    partner_users = get_partner_users(db)
    owner_candidates_map = {}
    for u in (proper_users + partner_users):
        owner_candidates_map[u['username']] = {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        }
    owner_candidates = list(owner_candidates_map.values())

    # POST: 選択ID受取→申請フォーム表示
    if request.method == 'POST' and not request.form.get('action'):
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index_bp.index'))
        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        items = [dict(row) for row in items]
        department = g.user['department']
        # manager権限ユーザーリスト取得
        all_managers = get_managers_by_department(None, db)
        # 並べ替え：同部門→他部門
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''

        # 管理者欄のデフォルト
        manager_default = g.user['username']

        return render_template(
            'entry_request.html',
            items=items, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            proper_users=proper_users_json,
            owner_candidates=owner_candidates,
            manager_default=manager_default,
            g=g,
        )

    # 申請フォーム送信（action=submit: 必須項目チェック＆申請内容をitem_applicationに登録、item.statusのみ即更新）
    if (request.method == 'GET' and request.args.get('action') == 'submit') or \
       (request.method == 'POST' and request.form.get('action') == 'submit'):
        form = request.form if request.method == 'POST' else request.args

        item_ids = form.getlist('item_id')
        if not item_ids:
            flash("申請対象が不正です")
            return redirect(url_for('index_bp.index'))

        # 必須入力チェック
        manager = form.get('manager', '').strip()
        comment = form.get('comment', '').strip()
        approver = form.get('approver', '').strip()
        qty_checked = form.getlist('qty_checked')
        with_checkout = form.get("with_checkout") == "1"

        # ▼ 新規：譲渡申請情報の取得
        with_transfer = form.get("with_transfer") == "1" if with_checkout else False
        transfer_branch_ids = form.getlist("transfer_branch_ids") if with_checkout and with_transfer else []
        transfer_comment = form.get("transfer_comment", "") if with_checkout and with_transfer else ""
        transfer_date = form.get("transfer_date", "") if with_checkout and with_transfer else ""

        errors = []
        if not manager or manager not in proper_usernames:
            errors.append("管理者の入力が不正です。正しい管理者を選択してください。")
        if not approver:
            errors.append("承認者を選択してください。")
        if len(qty_checked) != len(item_ids):
            errors.append("すべての数量チェックをしてください。")
        if with_checkout and with_transfer:
            if not transfer_branch_ids:
                errors.append("譲渡する枝番を選択してください。")
            if not transfer_comment.strip():
                errors.append("譲渡コメントを入力してください。")

        if errors:
            items = db.execute(
                f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
            ).fetchall()
            items = [dict(row) for row in items]
            department = g.user['department']
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''
            manager_default = g.user['username']
            error_dialog_message = " ".join(errors)  # または "\n".join(errors)
            return render_template(
                'entry_request.html',
                items=items, fields=INDEX_FIELDS,
                approver_default=approver_default,
                approver_list=sorted_managers,
                proper_users=proper_users_json,
                owner_candidates=owner_candidates,
                manager_default=manager_default,
                g=g,
                error_dialog_message=error_dialog_message,
            )

        # ▼ ステータス分岐
        if with_checkout and with_transfer:
            new_status = "入庫持ち出し譲渡申請中"
        elif with_checkout:
            new_status = "入庫持ち出し申請中"
        else:
            new_status = "入庫申請中"

        applicant = g.user['username']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_date = form.get('start_date', '')
        end_date = form.get('end_date', '')

        # 持ち出し同時申請時の所有者欄
        owner_lists = {}
        for id in item_ids:
            owner_lists[str(id)] = form.getlist(f'owner_list_{id}')

        for id in item_ids:
            # item_applicationに申請内容を登録
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            original_status = item['status']

            # itemのstatusのみ即時変更
            db.execute("UPDATE item SET status=? WHERE id=?", (new_status, id))

            # item内容を取得し、申請内容をnew_values(dict)として用意
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            new_values = dict(item)
            new_values['sample_manager'] = manager
            # ▼ 新規：譲渡申請情報
            if with_checkout and with_transfer:
                new_values['status'] = "入庫持ち出し譲渡申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])
                new_values['transfer_date'] = transfer_date
                # transfer_branch_ids 形式は itemID_branchNo。ここではitemごとに格納
                transfer_ids_this = []
                for t in transfer_branch_ids:
                    # t = "itemID_branchNo" → このitemの分だけ抽出
                    try:
                        tid, branch_no = t.split("_")
                        if str(tid) == str(id):
                            transfer_ids_this.append(int(branch_no))
                    except Exception:
                        continue
                new_values['transfer_branch_nos'] = transfer_ids_this  # このitemについての譲渡枝番リスト
                new_values['transfer_comment'] = transfer_comment
            elif with_checkout:
                new_values['status'] = "入庫持ち出し申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])
            else:
                new_values['status'] = "入庫申請中"

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id, json.dumps(new_values, ensure_ascii=False), applicant, comment, approver, "申請中", now_str, original_status
            ))

        db.commit()

        # ==== メール送信部（承認者・申請者・管理者・※with_checkout時は所有者も）====

        # 件名を種類別に
        if with_checkout and with_transfer:
            subject_kind = "入庫持ち出し譲渡申請"
        elif with_checkout:
            subject_kind = "入庫持ち出し申請"
        else:
            subject_kind = "入庫申請"

        # 対象 item と表示用の changes を組み立て
        # - manager はフォームで選択された単一 username
        # - owners は item ごとの owner_list（with_checkout のときのみ）
        # - transfer 枝番は with_transfer のときのみ
        from collections import defaultdict

        # item の管理者はフォーム指定の単一ユーザー
        manager_username = manager

        # itemごとの owners（username配列）
        owners_by_item = {}
        if with_checkout:
            for id in item_ids:
                owners_by_item[int(id)] = [u for u in owner_lists.get(str(id), []) if u]

        # itemごとの譲渡枝番（整数リスト）
        transfer_branches_by_item = defaultdict(list)
        if with_checkout and with_transfer and transfer_branch_ids:
            for t in transfer_branch_ids:  # "itemID_branchNo" 形式
                try:
                    tid, branch_no = t.split("_")
                    transfer_branches_by_item[int(tid)].append(int(branch_no))
                except Exception:
                    continue
            # 並びを整える
            for k in list(transfer_branches_by_item.keys()):
                transfer_branches_by_item[k] = sorted(transfer_branches_by_item[k])

        # changes 構造
        changes = []
        for id in item_ids:
            iid = int(id)
            changes.append({
                "item_id": iid,
                "manager": manager_username,  # username（本文では profiles で部署/氏名へ引き直す）
                "owners": owners_by_item.get(iid, []),  # [username]
                "transfer_branch_nos": transfer_branches_by_item.get(iid, []),  # [int]
            })

        # プロフィールを一括取得（宛先・本文用）
        usernames_to_fetch = {approver, applicant, manager_username}
        if with_checkout:
            all_owner_usernames = {u for arr in owners_by_item.values() for u in arr}
            usernames_to_fetch |= all_owner_usernames

        profiles = get_user_profiles(db, list(usernames_to_fetch))
        approver_prof  = profiles.get(approver,  {})
        applicant_prof = profiles.get(applicant, {})
        manager_prof   = profiles.get(manager_username, {})
        owner_profs    = []
        if with_checkout:
            # 重複除去した所有者を部門・氏名順に出したければソートキー調整もOK
            owner_profs = [profiles[u] for u in sorted({u for arr in owners_by_item.values() for u in arr})]

        # 宛先（重複除去）
        to_emails = set()
        for p in [approver_prof, applicant_prof, manager_prof] + (owner_profs if with_checkout else []):
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        # 件名
        subject = f"[申請] {subject_kind}の保存（{len(changes)}件）"

        # 本文（usernameは本文に出さない）
        body = render_template(
            "mails/entry_request.txt",
            approver_prof=approver_prof,
            applicant_prof=applicant_prof,
            manager_prof=manager_prof,
            owner_profs=owner_profs,      # with_checkout のときのみ非空
            changes=changes,
            profiles=profiles,            # manager/owners を部署・氏名へ引くため
            with_checkout=with_checkout,
            with_transfer=with_transfer,
            start_date=start_date,
            end_date=end_date,
            comment=comment,
            transfer_comment=transfer_comment,
            transfer_date=transfer_date,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"{subject_kind}を保存しました。承認待ちです。承認者ほか関係者にメールで連絡しました。")
        else:
            flash(f"{subject_kind}を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")
        # ==== ここまで ====

        if request.form.get('from_menu') or request.args.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index_bp.index'))

    return redirect(url_for('index_bp.index'))


@app.route('/return_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def return_request():
    db = get_db()

    # realname 優先の表示名マップ（indexと同じ）
    user_rows = db.execute("""
        SELECT username,
            COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    # 申請フォーム表示（POST:選択済みID受取→フォーム表示）
    if request.method == 'POST':
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index_bp.index'))
        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        not_accepted = [str(row['id']) for row in items if row['status'] != "持ち出し中"]
        if not_accepted:
            flash(f"持ち出し中でないアイテム（ID: {', '.join(not_accepted)}）は持ち出し終了申請できません。")
            return redirect(url_for('index_bp.index'))

        item_list = []
        for item in items:
            item = dict(item)
            item_id = item['id']
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?", (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item['sample_count'] = item.get('num_of_samples', 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item['sample_count'] = cnt
            item_list.append(item)

        department = g.user['department']
        all_managers = get_managers_by_department(None, db)
        # 並び順: 同じ部門→それ以外
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''
        return render_template(
            'return_form.html',
            items=item_list, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            user_display=user_display,
        )

    # 申請フォームからの送信時（GET, action=submit）
    if request.args.get('action') == 'submit':
        item_ids = request.args.getlist('item_id')
        checkeds = request.args.getlist('qty_checked')
        if not checkeds or len(checkeds) != len(item_ids):
            flash("全ての数量チェックを確認してください")
            return redirect(url_for('index_bp.index'))

        applicant = g.user['username']
        applicant_comment = request.args.get('comment', '')
        approver = request.args.get('approver', '')
        return_date = request.args.get('return_date', datetime.now().strftime("%Y-%m-%d"))
        storage = request.args.get('storage', '')

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for id in item_ids:
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            original_status = item['status']

            db.execute("UPDATE item SET status=? WHERE id=?", ("返却申請中", id))
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            new_values = dict(item)
            new_values['return_date'] = return_date
            new_values['storage'] = storage
            new_values['status'] = "返却申請中"

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id, json.dumps(new_values, ensure_ascii=False), applicant, applicant_comment, approver, "申請中", now_str, original_status
            ))
        db.commit()

        # ==== メール送信部（承認者・申請者・管理者）====

        # 対象 item の管理者（username）を収集
        items_rows = db.execute(
            f"SELECT id, sample_manager FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        manager_usernames = {row['sample_manager'] for row in items_rows if row and row['sample_manager']}

        # 申請者・承認者・管理者のプロフィールを一括取得
        usernames_to_fetch = {approver, applicant} | manager_usernames
        profiles = get_user_profiles(db, list(usernames_to_fetch))

        approver_prof  = profiles.get(approver,  {})
        applicant_prof = profiles.get(applicant, {})
        manager_profs  = [profiles[u] for u in sorted(manager_usernames)]

        # 宛先（重複除去）
        to_emails = set()
        for p in [approver_prof, applicant_prof] + manager_profs:
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        # 表示用の changes（itemとその管理者）
        changes = [{"item_id": row["id"], "manager": row["sample_manager"]} for row in items_rows]

        # 件名
        subject = f"[申請] 返却申請の保存（{len(changes)}件）"

        # 本文（usernameは本文に出さない）
        body = render_template(
            "mails/return_request.txt",
            approver_prof=approver_prof,
            applicant_prof=applicant_prof,
            manager_profs=manager_profs,
            changes=changes,
            profiles=profiles,          # manager の部署/氏名へ変換に使用
            return_date=return_date,
            storage=storage,
            applicant_comment=applicant_comment,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash("返却申請を保存しました。承認待ちです。承認者ほか関係者にメールで連絡しました。")
        else:
            flash("返却申請を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")
        # ==== ここまで ====

        if request.args.get('from_menu') or request.form.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index_bp.index'))
    
    return redirect(url_for('index_bp.index'))


@app.route('/bulk_manager_change', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def bulk_manager_change():
    ids_str = request.args.get('ids') if request.method == 'GET' else request.form.get('ids')
    if not ids_str:
        flash("通し番号が指定されていません")
        return redirect(url_for('index_bp.index'))

    id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]
    db = get_db()
    items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
    proper_users = get_proper_users(db)
    proper_usernames = [u['username'] for u in proper_users]
    proper_users_json = [
        {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', ''),
            'email': u.get('email', '')
        } for u in proper_users
    ]

    # ▼ 一覧表示用に現在の管理者プロフィールを取得（表示フォーマット：department realname）
    current_manager_usernames = list({item['sample_manager'] for item in items})
    display_profiles = get_user_profiles(db, current_manager_usernames)

    if request.method == 'POST':
        new_manager = request.form.get('new_manager', '').strip()
        if not new_manager or new_manager not in proper_usernames:
            # ← エラー表示時も profiles を渡す（テンプレでフォーマットに利用）
            items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
            current_manager_usernames = list({item['sample_manager'] for item in items})
            display_profiles = get_user_profiles(db, current_manager_usernames)

            return render_template(
                'bulk_manager_change.html',
                items=items, ids=ids_str,
                proper_users=proper_users_json,
                manager_default=g.user['username'],
                profiles=display_profiles,
                error_message="管理者は候補から選択してください。"
            )

        # 旧管理者（変更前）を確定させるため、更新前 items を使って set 化
        old_managers = set(item['sample_manager'] for item in items)

        # 一括更新
        db.executemany("UPDATE item SET sample_manager=? WHERE id=?", [(new_manager, item['id']) for item in items])
        db.commit()

        # === ここから通知メール作成 ===
        # 旧管理者 + 新管理者 のプロフィールをまとめて取得
        usernames_to_fetch = list(old_managers | {new_manager})
        profiles = get_user_profiles(db, usernames_to_fetch)

        new_prof = profiles.get(new_manager, {"email": "", "realname": "", "department": ""})
        old_profs = [profiles[u] for u in old_managers if u in profiles]

        # 宛先: 旧管理者全員 + 新管理者 + 操作した本人
        to_emails = {p["email"] for p in old_profs if p.get("email")}
        if new_prof.get("email"):
            to_emails.add(new_prof["email"])

        # 変更者（ログインユーザー）
        changer_prof = get_user_profile(db, g.user["username"])
        if changer_prof.get("email"):
            to_emails.add(changer_prof["email"])
        
        subject = f"[通知] 管理者一括変更 ({len(items)}件)"
        body = render_template(
            "mails/manager_change.txt",
            new_prof=new_prof,
            old_profs=old_profs,
            items=items,
            profiles=profiles,
            changer_prof=changer_prof
        )

        to = ",".join(sorted(to_emails))
        result = send_mail(to=to, subject=subject, body=body)

        if result:
            flash(f"管理者を「{new_manager}」に一括変更しました。旧管理者・新管理者にメールで連絡しました。")
        else:
            flash(f"管理者を「{new_manager}」に一括変更しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        if request.form.get('from_menu') or request.args.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index_bp.index'))
        
    manager_default = g.user['username']
    return render_template(
        'bulk_manager_change.html',
        items=items, ids=ids_str,
        proper_users=proper_users_json,
        manager_default=manager_default,
        profiles=display_profiles,
        error_message=None
    )


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
    
@app.route('/change_owner', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def change_owner():
    db = get_db()

    # 候補ユーザー（proper + partner）
    proper_users = get_proper_users(db)
    partner_users = get_partner_users(db)
    tmp = {}
    for u in (proper_users + partner_users):
        tmp[u['username']] = {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        }
    owner_candidates = list(tmp.values())

    ids_str = request.args.get('ids') if request.method == 'GET' else request.form.get('ids')
    if not ids_str:
        flash("通し番号が指定されていません")
        return redirect(url_for('index_bp.index'))
    id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]

    # 対象item（持ち出し中のみ）
    items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
    target_ids = [item['id'] for item in items if item['status'] == "持ち出し中"]
    if not target_ids:
        flash("選択した中に所有者変更できるアイテムがありません（持ち出し中のみ可能）")
        return redirect(url_for('index_bp.index'))

    # 子アイテム取得（枝番順）
    child_items = db.execute(
        f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(target_ids))}) ORDER BY item_id, branch_no",
        target_ids
    ).fetchall()

    # ★ GET: 一覧表示用に「現在の所有者」のプロフィールを用意
    if request.method == 'GET':
        current_owner_usernames = [ci['owner'] for ci in child_items if ci['owner']]
        owner_profiles = get_user_profiles(db, list(set(current_owner_usernames)))  # dict[username] -> {department, realname, ...}

        return render_template(
            'change_owner.html',
            items=items,
            child_items=child_items,
            ids=','.join(str(i) for i in target_ids),
            owner_candidates=owner_candidates,
            profiles=owner_profiles,
        )

    # POST: 変更反映
    updates = []
    for ci in child_items:
        owner_key = f"owner_{ci['item_id']}_{ci['branch_no']}"
        new_owner = request.form.get(owner_key, '').strip()
        if new_owner and new_owner != ci['owner']:
            updates.append((new_owner, ci['id']))
    if updates:
        db.executemany("UPDATE child_item SET owner=? WHERE id=?", updates)
        db.commit()
        # 履歴記録
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for ci, (new_owner, _) in zip(child_items, updates):
            db.execute('''
                INSERT INTO application_history
                (item_id, applicant, application_content, applicant_comment, application_datetime, approver, status, approval_datetime, approver_comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ci['item_id'], g.user['username'],
                "所有者変更", f"{ci['branch_no']}番 所有者: {ci['owner']}→{new_owner}",
                now_str, "", "承認不要", now_str, ""
            ))
        db.commit()

        # メール通知
        updates_map = {ci_id: new_owner for (new_owner, ci_id) in updates}

        items_by_id = {it['id']: it for it in items}  # item_id -> sqlite3.Row

        changes = []
        manager_usernames = set()
        old_owner_usernames = set()
        new_owner_usernames = set()

        def row_get(row, key, default=""):
            """sqlite3.Row 安全アクセス"""
            if not row:
                return default
            try:
                val = row[key]
                return default if val is None else val
            except Exception:
                return default

        for ci in child_items:
            if ci['id'] not in updates_map:
                continue  # 更新なし
            new_owner = updates_map[ci['id']]
            old_owner = ci['owner']
            if new_owner == old_owner:
                continue

            item = items_by_id.get(ci['item_id'])
            # Row から安全に取り出す（.get は使わない）
            manager = row_get(item, 'sample_manager', '')

            changes.append({
                "item_id": ci['item_id'],
                "branch_no": ci['branch_no'],
                "manager": manager,
                "old_owner": old_owner,
                "new_owner": new_owner,
            })

            if manager:
                manager_usernames.add(manager)
            if old_owner:
                old_owner_usernames.add(old_owner)
            if new_owner:
                new_owner_usernames.add(new_owner)

        # 変更者
        changer_username = g.user['username']

        # プロフィールを一括取得
        all_usernames = (
            {changer_username} |
            manager_usernames |
            old_owner_usernames |
            new_owner_usernames
        )
        profiles = get_user_profiles(db, list(all_usernames))

        changer_prof = profiles.get(changer_username, {})
        manager_profs = [profiles[u] for u in sorted(manager_usernames)]
        old_owner_profs = [profiles[u] for u in sorted(old_owner_usernames)]
        new_owner_profs = [profiles[u] for u in sorted(new_owner_usernames)]

        # 宛先（重複除去）
        to_emails = set()
        for p in manager_profs + old_owner_profs + new_owner_profs + [changer_prof]:
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        subject = f"[通知] 所有者変更 ({len(changes)}件)"

        body = render_template(
            "mails/owner_change.txt",
            changer_prof=changer_prof,
            manager_profs=manager_profs,
            old_owner_profs=old_owner_profs,
            new_owner_profs=new_owner_profs,
            changes=changes,
            profiles=profiles,  # テンプレで department/realname 参照用
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash("所有者を変更しました。所有者・管理者・変更者にメールで連絡しました。")
        else:
            flash("所有者を変更しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

    else:
        flash("変更はありませんでした。")
    return redirect(url_for('index_bp.index'))


@app.route('/dispose_transfer_request', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper')
def dispose_transfer_request():
    db = get_db()

    # 対応者（handler）候補：proper のみ
    proper_users = get_proper_users(db)  # [{'username','department','realname'},...]
    proper_users_json = [
        {'username': u['username'], 'department': u.get('department',''), 'realname': u.get('realname','')}
        for u in proper_users
    ]
    handler_default = g.user['username']

    # index と同じ realname 優先の表示名マップ
    user_rows = db.execute("""
        SELECT username,
            COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    #（所有者用：department + realname 形式／realname 空なら username）
    user_rows2 = db.execute("""
        SELECT
            username,
            TRIM(
                COALESCE(NULLIF(department,''),'') || ' ' ||
                COALESCE(NULLIF(realname,''), username)
            ) AS display_name
        FROM users
    """).fetchall()
    user_display_dept = {r["username"]: r["display_name"] for r in user_rows2}

    # POST: 申請フォーム送信
    if request.method == 'POST' and request.form.get('action') == 'submit':
        item_ids = request.form.getlist('item_id')
        dispose_type = request.form.get('dispose_type', '')
        handler = request.form.get('handler', '').strip()
        dispose_date = request.form.get("dispose_date", '')
        dispose_comment = request.form.get('dispose_comment', '').strip()
        applicant_comment = request.form.get('comment', '').strip()
        approver = request.form.get('approver', '').strip()
        target_child_ids = request.form.getlist('target_child_ids')
        qty_checked_ids = []
        for item_id in item_ids:
            if request.form.get(f'qty_checked_{item_id}'):
                qty_checked_ids.append(item_id)
        errors = []

        if not item_ids:
            errors.append("申請対象アイテムがありません。")
        if not dispose_type:
            errors.append("破棄か譲渡の種別を選択してください。")
        if not handler:
            errors.append("対応者を入力してください。")
        if not approver:
            errors.append("承認者を選択してください。")
        if not target_child_ids:
            errors.append("少なくとも1つの子アイテムを選択してください。")
        if len(qty_checked_ids) != len(item_ids):
            errors.append("すべての親アイテムで数量チェックをしてください。")

        if errors:
            items = db.execute(
                f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
            ).fetchall()
            item_list = []
            for item in items:
                item = dict(item)
                item_id = item['id']
                child_total = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=?", (item_id,)
                ).fetchone()[0]
                if child_total == 0:
                    item['sample_count'] = item.get('num_of_samples', 0)
                else:
                    cnt = db.execute(
                        "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                        (item_id, "破棄", "譲渡")
                    ).fetchone()[0]
                    item['sample_count'] = cnt
                item_list.append(item)
            child_items = db.execute(
                f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(item_ids))}) ORDER BY item_id, branch_no",
                item_ids
            ).fetchall()
            for msg in errors:
                flash(msg)
            department = g.user['department']
            managers_same_dept = get_managers_by_department(department, db)
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''
            return render_template(
                'dispose_transfer_form.html',
                items=item_list, child_items=child_items, fields=INDEX_FIELDS,
                approver_default=approver_default,
                approver_list=sorted_managers,
                proper_users=proper_users_json,
                handler_default=handler_default,
                user_display=user_display,
                user_display_dept=user_display_dept
            )

        applicant = g.user['username']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item_id in item_ids:
            item = db.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
            item_dict = dict(item)
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?", (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item_dict['sample_count'] = item_dict.get('num_of_samples', 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item_dict['sample_count'] = cnt

            new_values = dict(item_dict)
            new_values['dispose_type'] = dispose_type
            new_values['dispose_date'] = dispose_date
            new_values['handler'] = handler
            new_values['dispose_comment'] = dispose_comment

            target_child_branches_this = []
            for cid in target_child_ids:
                row = db.execute("SELECT item_id, branch_no FROM child_item WHERE id=?", (cid,)).fetchone()
                if row and row['item_id'] == int(item_id):
                    target_child_branches_this.append({"id": cid, "branch_no": row['branch_no']})

            new_values['target_child_branches'] = target_child_branches_this
            new_values['status'] = "破棄・譲渡申請中"

            item = db.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
            original_status = item['status']

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item_id, json.dumps(new_values, ensure_ascii=False), applicant,
                applicant_comment, approver, "申請中", now_str, original_status
            ))
            db.execute("UPDATE item SET status=? WHERE id=?", ("破棄・譲渡申請中", item_id))
        db.commit()

        # ==== メール送信部（承認者・申請者・管理者）====

        # 申請に含まれる item の管理者（username）を収集
        items_rows = db.execute(
            f"SELECT id, sample_manager FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        manager_usernames = {row['sample_manager'] for row in items_rows if row and row['sample_manager']}

        # 申請者・承認者・管理者（username）をまとめてプロフィール取得
        applicant = g.user['username']  # 既に上で定義済みですが念のため
        usernames_to_fetch = {approver, applicant} | manager_usernames
        profiles = get_user_profiles(db, list(usernames_to_fetch))

        approver_prof  = profiles.get(approver,  {})
        applicant_prof = profiles.get(applicant, {})
        manager_profs  = [profiles[u] for u in sorted(manager_usernames)]

        # 宛先（重複除去）: 承認者・申請者・管理者
        to_emails = set()
        for p in [approver_prof, applicant_prof] + manager_profs:
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        # 本文に載せる「対象アイテムと枝番」の一覧を作成
        # target_child_ids は全体の子アイテムIDリストなので、item_id ごとにまとめ直す
        from collections import defaultdict
        branches_by_item = defaultdict(list)
        if target_child_ids:
            rows = db.execute(
                f"SELECT id, item_id, branch_no FROM child_item WHERE id IN ({','.join(['?']*len(target_child_ids))})",
                target_child_ids
            ).fetchall()
            for r in rows:
                branches_by_item[r['item_id']].append(r['branch_no'])

        # 表示用の changes 構造（テンプレに渡す）
        changes = []
        for row in items_rows:
            item_id = row['id']
            manager  = row['sample_manager']
            branch_nos = sorted(branches_by_item.get(item_id, []))
            changes.append({
                "item_id": item_id,
                "manager": manager,          # username（本文では profiles[...] で部署/氏名に変換）
                "branch_nos": branch_nos,    # [int]
            })

        # 件名
        subject = f"[申請] 破棄・譲渡申請の保存（{len(changes)}件）"

        # 本文（usernameは本文に出さない）
        body = render_template(
            "mails/dispose_transfer_request.txt",
            approver_prof=approver_prof,
            applicant_prof=applicant_prof,
            manager_profs=manager_profs,
            changes=changes,
            profiles=profiles,          # manager の部署/氏名へ lookup 用
            dispose_type=dispose_type,  # 破棄 or 譲渡
            dispose_date=dispose_date,
            handler=handler,            # 対応者の username（本文では出さないが、必要なら後で使える）
            dispose_comment=dispose_comment,
            applicant_comment=applicant_comment,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash("破棄・譲渡申請を保存しました。承認待ちです。承認者ほか関係者にメールで連絡しました。")
        else:
            flash("破棄・譲渡申請を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")
        # ==== ここまで ====

        if request.form.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index_bp.index'))

    # 申請画面表示（POST/GET共通: item_idリストで遷移）
    if request.method == 'POST':
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index_bp.index'))
        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        item_list = []
        for item in items:
            item = dict(item)
            item_id = item['id']
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?", (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item['sample_count'] = item.get('num_of_samples', 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item['sample_count'] = cnt
            item_list.append(item)

        child_items = db.execute(
            f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(item_ids))}) ORDER BY item_id, branch_no",
            item_ids
        ).fetchall()

        department = g.user['department']
        managers_same_dept = get_managers_by_department(department, db)
        all_managers = get_managers_by_department(None, db)
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''
        return render_template(
            'dispose_transfer_form.html',
            items=item_list, child_items=child_items, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            proper_users=proper_users_json,
            handler_default=handler_default,
            user_display=user_display,
            user_display_dept=user_display_dept
        )

    return redirect(url_for('index_bp.index'))


@app.route('/inventory_list')
@login_required
@roles_required('admin', 'manager', 'proper')
def inventory_list():
    db = get_db()
    username = g.user['username']
    user_roles = g.user_roles

    if 'admin' in user_roles or 'manager' in user_roles:
        # 全件表示
        items = db.execute('''
            SELECT i.*, 
                ic.checked_at as last_checked_at, 
                ic.checker as last_checker
            FROM item i
            LEFT JOIN (
                SELECT a.item_id, a.checked_at, a.checker
                FROM inventory_check a
                INNER JOIN (
                    SELECT item_id, MAX(checked_at) as max_checked
                    FROM inventory_check GROUP BY item_id
                ) b
                ON a.item_id = b.item_id AND a.checked_at = b.max_checked
            ) ic ON i.id = ic.item_id
            ORDER BY i.id DESC
        ''').fetchall()
    elif 'proper' in user_roles:
        # 管理者が自分だけを表示（sample_manager列が自分のユーザー名）
        items = db.execute('''
            SELECT i.*, 
                ic.checked_at as last_checked_at, 
                ic.checker as last_checker
            FROM item i
            LEFT JOIN (
                SELECT a.item_id, a.checked_at, a.checker
                FROM inventory_check a
                INNER JOIN (
                    SELECT item_id, MAX(checked_at) as max_checked
                    FROM inventory_check GROUP BY item_id
                ) b
                ON a.item_id = b.item_id AND a.checked_at = b.max_checked
            ) ic ON i.id = ic.item_id
            WHERE i.sample_manager = ?
            ORDER BY i.id DESC
        ''', (username,)).fetchall()
    else:
        # partnerや他ロールはここで制御
        items = []

    # ★ index と同じ realname 優先の表示名マップ
    user_rows = db.execute("""
        SELECT username,
               COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    return render_template('inventory_list.html',
                           items=items,
                           fields=INDEX_FIELDS,
                           user_display=user_display)


@app.route('/inventory_check', methods=['POST'])
@login_required
@roles_required('admin', 'manager', 'proper')
def inventory_check():
    db = get_db()
    item_ids = request.form.getlist('selected_ids')
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for id in item_ids:
        db.execute(
            "INSERT INTO inventory_check (item_id, checked_at, checker) VALUES (?, ?, ?)",
            (id, now, g.user['username'])
        )
    db.commit()
    flash('棚卸しを登録しました')
    return redirect(url_for('inventory_list'))


@app.route('/inventory_history/<int:item_id>')
@login_required
@roles_required('admin', 'manager', 'proper')
def inventory_history(item_id):
    db = get_db()

    # 履歴とアイテム本体
    rows = db.execute(
        "SELECT * FROM inventory_check WHERE item_id=? ORDER BY checked_at DESC",
        (item_id,)
    ).fetchall()
    item = db.execute(
        "SELECT * FROM item WHERE id=?", (item_id,)
    ).fetchone()

    # ★ department realname 形式（realname が空なら username で補完）
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
        'inventory_history.html',
        rows=rows,
        item=item,
        user_display=user_display,
    )


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


@app.route('/bulk_edit')
@login_required
def bulk_edit():
    """
    index から選択したID群を ?ids=1,2,3 で受けてロック→編集画面へ。
    画面表示で sample_manager を index と同じ user_display マップで表示するため、
    users テーブルから display_name を構築してテンプレへ渡す。
    """
    ids_raw = request.args.get('ids', '')
    ids = [s for s in ids_raw.split(',') if s.strip()]
    if not ids:
        flash("編集対象の通し番号を選択してください。")
        return redirect(url_for('index_bp.index'))

    db = get_db()

    # 他ユーザーによる編集中チェック＆ロック取得
    ok, blocked = acquire_locks(db, ids, g.user['username'])
    if not ok:
        flash("以下のIDは他ユーザーが編集中のため編集できません: " + ", ".join(blocked))
        return redirect(url_for('index_bp.index'))

    # ★ index と同じ user_display を作成
    user_rows = db.execute("""
        SELECT
            username,
            COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    # 編集対象の取得（index と同じ項目セット）
    user_field_keys = [f['key'] for f in FIELDS if not f.get('internal', False) and f.get('show_in_index', True)]
    placeholders = ",".join(["?"] * len(ids))
    rows = db.execute(
        f"SELECT * FROM item WHERE id IN ({placeholders}) ORDER BY id DESC",
        ids
    ).fetchall()
    items = [dict(r) for r in rows]

    return render_template(
        'edit.html',
        items=items,
        fields=[f for f in FIELDS if f.get('show_in_index', True)],
        user_field_keys=user_field_keys,
        user_display=user_display,
    )

@app.route('/bulk_edit/commit', methods=['POST'])
@login_required
def bulk_edit_commit():
    db = get_db()
    ids = request.form.getlist('item_id')

    user_field_keys = [f['key'] for f in FIELDS if not f.get('internal', False) and f.get('show_in_index', True)]

    for item_id in ids:
        values = [request.form.get(f"{key}_{item_id}", "") for key in user_field_keys]
        set_clause = ", ".join([f"{key}=?" for key in user_field_keys])
        db.execute(f"UPDATE item SET {set_clause} WHERE id=?", values + [item_id])

    db.commit()
    # ロックのみ解除（statusは触らない）
    release_locks(db, [int(x) for x in ids])
    flash(f"{len(ids)}件の編集を反映しました。")
    return redirect(url_for('index_bp.index'))


@app.route('/bulk_edit/cancel', methods=['POST'])
@login_required
def bulk_edit_cancel():
    db = get_db()
    ids = request.form.getlist('item_id')

    # 値は保存せず、ロックのみ解除（statusは触らない）
    release_locks(db, [int(x) for x in ids])
    flash("編集をキャンセルし、ロックを解除しました。")
    return redirect(url_for('index_bp.index'))


@app.route('/unlock_my_locks', methods=['POST'])
@login_required
def unlock_my_locks():
    db = get_db()
    # 自分がロックしている全IDを収集して解除
    rows = db.execute("SELECT id FROM item WHERE locked_by=?", (g.user['username'],)).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        release_locks(db, ids)
        flash(f"{len(ids)}件のロックを解除しました。")
    else:
        flash("解除対象のロックはありません。")
    return redirect(url_for('index_bp.index'))


if __name__ == '__main__':
    init_db()
    init_user_db()
    init_child_item_db()
    init_checkout_history_db()
    init_item_application_db()
    init_application_history_db()
    init_inventory_check_db()
    app.run(debug=True, host='0.0.0.0', port=8000)
