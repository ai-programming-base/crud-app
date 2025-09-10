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

    is_admin = ('admin' in g.user_roles)
    is_manager = ('manager' in g.user_roles)

    if request.method == 'POST':
        selected_roles = request.form.getlist('roles')

        if is_admin:
            # --- 管理者: 各種プロフィールを更新可（ユーザー名は変更しない） ---
            password = request.form.get('password', '')
            email = (request.form.get('email') or '').strip()
            department = (request.form.get('department') or '').strip()
            realname = (request.form.get('realname') or '').strip()

            # バリデーション：メールのみ必須（ユーザー名は不変のためチェック不要）
            if not email:
                flash('メールは必須です')
            else:
                if password:
                    db.execute(
                        "UPDATE users SET password=?, email=?, department=?, realname=? WHERE id=?",
                        (generate_password_hash(password), email, department, realname, user_id)
                    )
                else:
                    db.execute(
                        "UPDATE users SET email=?, department=?, realname=? WHERE id=?",
                        (email, department, realname, user_id)
                    )

                # ロール更新
                db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
                for role_id in selected_roles:
                    db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))

                db.commit()
                flash('ユーザー情報を更新しました')
                return redirect(url_for('edit_user', user_id=user_id))

        elif is_manager:
            # --- マネージャ: ロールのみ更新。ユーザー情報は無視 ---
            db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
            for role_id in selected_roles:
                db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
            db.commit()
            flash('ロールを更新しました')
            return redirect(url_for('edit_user', user_id=user_id))

    # 再読込（GET/バリデーションNG）
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
