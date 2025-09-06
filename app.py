import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json
from datetime import datetime, timedelta, timezone

import logging
from logging.handlers import RotatingFileHandler
import sys

from auth import authenticate
from send_mail import send_mail

app = Flask(__name__)
app.secret_key = "any_secret"

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

DATABASE = 'items.db'

# フィールド定義（fields.jsonを利用）
FIELDS_PATH = os.path.join(os.path.dirname(__file__), 'fields.json')
with open(FIELDS_PATH, encoding='utf-8') as f:
    FIELDS = json.load(f)
USER_FIELDS = [f for f in FIELDS if not f.get('internal')]
INDEX_FIELDS = [f for f in FIELDS if f.get('show_in_index')]
FIELD_KEYS = [f['key'] for f in FIELDS]

logger.debug(FIELDS)

SELECT_FIELD_PATH = os.path.join(os.path.dirname(__file__), 'select_fields.json')

LOCK_TTL_MIN = 30  # ロック有効期限（分）

def load_select_fields():
    if os.path.exists(SELECT_FIELD_PATH):
        with open(SELECT_FIELD_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_select_fields(data):
    with open(SELECT_FIELD_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

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

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def roles_required(*roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_roles = getattr(g, 'user_roles', [])
            if not any(role in user_roles for role in roles):
                flash('権限がありません')
                return redirect(url_for('index'))
            return func(*args, **kwargs)
        return wrapper
    return decorator

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
            return redirect(url_for('index'))
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


def get_managers_by_department(department=None, db=None):
    """
    department指定時→その部門のmanager全員
    department=None→全manager
    """
    if db is None:
        db = get_db()
    query = """
        SELECT u.username, u.realname, u.department
        FROM users u
        JOIN user_roles ur ON u.id = ur.user_id
        JOIN roles r ON ur.role_id = r.id
        WHERE r.name = 'manager'
    """
    params = []
    if department:
        query += " AND u.department = ?"
        params.append(department)
    query += " ORDER BY u.department, u.realname"
    rows = db.execute(query, params).fetchall()
    return [{'username': row['username'], 'realname': row['realname'], 'department': row['department']} for row in rows]


def get_proper_users(db):
    return [
        dict(username=row['username'], realname=row['realname'], department=row['department'])
        for row in db.execute(
            """SELECT u.username, u.realname, u.department
                 FROM users u
                 JOIN user_roles ur ON u.id = ur.user_id
                 JOIN roles r ON ur.role_id = r.id
                WHERE r.name = 'proper' ORDER BY u.department, u.username""")
    ]

def get_partner_users(db):
    return [
        dict(username=row['username'], realname=row['realname'], department=row['department'])
        for row in db.execute(
            """SELECT u.username, u.realname, u.department
                 FROM users u
                 JOIN user_roles ur ON u.id = ur.user_id
                 JOIN roles r ON ur.role_id = r.id
                WHERE r.name = 'partner' ORDER BY u.department, u.username""")
    ]

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
        return redirect(url_for('index'))

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

def _now():
    return datetime.now(timezone.utc)

def _is_lock_expired(locked_at_str):
    if not locked_at_str:
        return True
    try:
        t = datetime.fromisoformat(locked_at_str)
    except Exception:
        return True
    return (_now() - t) > timedelta(minutes=LOCK_TTL_MIN)

def acquire_locks(db, ids, username):
    """
    指定ID群に対しロックを取得。誰かがロック中かつ未期限切れなら失敗。
    ※ status は変更しない！
    戻り値: (ok:bool, blocked:list[str]) 取得できなかったID
    """
    blocked = []
    # 可能ならここで ids を int 正規化しておく
    norm_ids = []
    for item_id in ids:
        try:
            item_id = int(item_id)
        except Exception:
            blocked.append(str(item_id))
            continue
        norm_ids.append(item_id)

        row = db.execute("SELECT locked_by, locked_at FROM item WHERE id=?", (item_id,)).fetchone()
        if not row:
            blocked.append(str(item_id)); continue
        lb, la = row["locked_by"], row["locked_at"]
        if lb and not _is_lock_expired(la) and lb != username:
            blocked.append(str(item_id))

    if blocked:
        return False, blocked

    # ロック取得（status は触らない）
    now = _now().isoformat()
    for item_id in norm_ids:
        db.execute(
            "UPDATE item SET locked_by=?, locked_at=? WHERE id=?",
            (str(username or ""), now, item_id)
        )
    db.commit()
    return True, []


def release_locks(db, ids):
    for item_id in ids:
        db.execute(
            "UPDATE item SET locked_by=NULL, locked_at=NULL WHERE id=?",
            (item_id,)
        )
    db.commit()


def _cleanup_expired_locks(db):
    # locked_byがNULLでも空文字でも「ロックされていない」と見なす
    rows = db.execute(
        "SELECT id, locked_at FROM item WHERE locked_by IS NOT NULL AND locked_by != ''"
    ).fetchall()
    expired_ids = [r["id"] for r in rows if _is_lock_expired(r["locked_at"])]
    if expired_ids:
        ph = ",".join(["?"]*len(expired_ids))
        db.execute(f"UPDATE item SET locked_by=NULL, locked_at=NULL WHERE id IN ({ph})", expired_ids)
        db.commit()


@app.route('/menu')
@login_required
def menu():
    return render_template('menu.html')


@app.route('/')
@login_required
def index():
    # ページ件数指定: ?per_page=10/20/50/100/all
    per_page_raw = request.args.get('per_page', '20')
    if per_page_raw == 'all':
        per_page = None
        page = 1
        offset = 0
    else:
        per_page = int(per_page_raw)
        page = int(request.args.get('page', 1))
        offset = (page - 1) * per_page

    db = get_db()
    _cleanup_expired_locks(db)

    user_rows = db.execute(
        """
        SELECT username,
               COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
        """
    ).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    logger.debug(user_display)

    # ===== フィルタ構築 =====
    filters = {}
    where = []
    params = []

    # (1) IDフィルタ（※INDEX_FIELDSにidが含まれていても動くが、二重追加を避けるため基本は含めない想定）
    id_filter = request.args.get("id_filter", "").strip()
    filters["id"] = id_filter
    if id_filter:
        where.append("CAST(id AS TEXT) LIKE ?")
        params.append(f"%{id_filter}%")

    # (2) 通常カラム（INDEX_FIELDS）フィルタ
    for f in INDEX_FIELDS:
        key = f["key"]
        v = request.args.get(f"{key}_filter", "").strip()
        filters[key] = v
        if v:
            where.append(f"{key} LIKE ?")
            params.append(f"%{v}%")

    # (3) サンプル数フィルタ（Python側で厳密一致）
    sample_count_filter = request.args.get("sample_count_filter", "").strip()
    filters["sample_count"] = sample_count_filter

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    # ===== データ取得 =====
    # サンプル数フィルタの有無で分岐（サンプル数は動的算出のため）
    if not sample_count_filter:
        # 件数
        total = db.execute(f"SELECT COUNT(*) FROM item {where_clause}", params).fetchone()[0]

        # ページング付き・なしの取得
        query = f"SELECT * FROM item {where_clause} ORDER BY id DESC"
        if per_page is not None:
            query += " LIMIT ? OFFSET ?"
            rows = db.execute(query, params + [per_page, offset]).fetchall()
        else:
            rows = db.execute(query, params).fetchall()

        # sample_count の付与
        item_list = []
        for row in rows:
            item = dict(row)
            item_id = item["id"]
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?",
                (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item["sample_count"] = item.get("num_of_samples", 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item["sample_count"] = cnt
            item_list.append(item)

    else:
        # サンプル数フィルタあり：対象候補をまず全件（ページング前）取得して Python 側で絞り込み
        rows = db.execute(
            f"SELECT * FROM item {where_clause} ORDER BY id DESC",
            params
        ).fetchall()

        items_all = []
        for row in rows:
            item = dict(row)
            item_id = item["id"]
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?",
                (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item["sample_count"] = item.get("num_of_samples", 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item["sample_count"] = cnt
            items_all.append(item)

        # 文字列一致（UI側も文字列でhiddenに入れているため）
        items_filtered = [it for it in items_all if str(it["sample_count"]) == sample_count_filter]

        total = len(items_filtered)
        if per_page is not None:
            item_list = items_filtered[offset:offset + per_page]
        else:
            item_list = items_filtered

    # ===== フィルタ候補の辞書 =====
    filter_choices_dict = {}

    # 通常カラムの候補
    for f in INDEX_FIELDS:
        col = f["key"]
        rows = db.execute(
            f"SELECT DISTINCT {col} FROM item WHERE {col} IS NOT NULL AND {col} != ''"
        ).fetchall()
        filter_choices_dict[col] = sorted({str(row[col]) for row in rows if row[col] not in (None, '')})

    # ID候補（任意：多すぎる場合はページ内のIDだけにするなど調整可）
    id_rows = db.execute("SELECT id FROM item ORDER BY id DESC").fetchall()
    filter_choices_dict["id"] = [str(r["id"]) for r in id_rows]

    # サンプル数候補：現在の表示リストから（UX的に現ページに存在する値に限定）
    filter_choices_dict["sample_count"] = sorted({str(item["sample_count"]) for item in item_list})

    # ===== ページ数 =====
    if per_page is not None:
        page_count = max(1, (total + per_page - 1) // per_page)
    else:
        page_count = 1

    return render_template(
        'index.html',
        items=item_list,
        page=page,
        page_count=page_count,
        filters=filters,
        total=total,
        fields=INDEX_FIELDS,
        filter_choices_dict=filter_choices_dict,
        per_page=per_page_raw,
        user_display=user_display,
    )


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
                return redirect(url_for('index'))

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
        return redirect(url_for('index'))


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
            return redirect(url_for('index'))
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
            return redirect(url_for('index'))

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

        # メール通知
        to=""
        subject=""
        body=""
        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"申請内容を保存しました。承認待ちです。承認者にメールで連絡しました。")
        else:
            flash(f"申請内容を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        if request.form.get('from_menu') or request.args.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index'))

    return redirect(url_for('index'))


@app.route('/checkout_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def checkout_request():
    db = get_db()

    # properユーザーリスト取得
    proper_users = get_proper_users(db)
    proper_usernames = [u['username'] for u in proper_users]
    proper_users_json = [
        {'username': u['username'], 'department': u.get('department', ''), 'realname': u.get('realname', '')}
        for u in proper_users
    ]
    manager_default = g.user['username']

    # partner + proper を所有者候補として統合
    partner_users = get_partner_users(db)
    tmp_map = {}
    for u in (proper_users + partner_users):
        tmp_map[u['username']] = {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        }
    owner_candidates = list(tmp_map.values())

    # 申請画面表示
    if request.method == 'POST' and not request.form.get('action'):
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index'))

        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        items = [dict(row) for row in items]

        # --- 利用可能枝番の付与 ---
        from collections import defaultdict
        placeholders = ','.join(['?'] * len(item_ids))
        child_rows = db.execute(
            f"SELECT item_id, branch_no, status FROM child_item WHERE item_id IN ({placeholders})",
            item_ids
        ).fetchall()

        eligible = defaultdict(list)
        for r in child_rows:
            if r['status'] not in ('破棄', '譲渡'):
                eligible[r['item_id']].append(r['branch_no'])

        for it in items:
            fallback_n = int(it.get('num_of_samples') or 1)
            fallback_branches = list(range(1, fallback_n + 1))
            branches = eligible.get(it['id'], fallback_branches)
            it['available_branches'] = sorted(branches)
            it['available_count'] = len(branches)

        items = [it for it in items if it['available_count'] > 0]
        if not items:
            flash("申請可能な枝番が存在しません（破棄・譲渡のみ）。")
            return redirect(url_for('index'))

        # --- 承認者リスト整形 ---
        department = g.user['department']
        all_managers = get_managers_by_department(None, db)
        # 並べ替え（同部門→それ以外）
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''

        return render_template(
            'checkout_form.html',
            items=items, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            proper_users=proper_users_json,
            owner_candidates=owner_candidates,
            manager_default=manager_default,
            g=g,
        )

    # 申請フォーム送信
    if (request.method == 'GET' and request.args.get('action') == 'submit') or \
       (request.method == 'POST' and request.form.get('action') == 'submit'):
        form = request.form if request.method == 'POST' else request.args

        item_ids = form.getlist('item_id')
        if not item_ids:
            flash("申請対象が不正です")
            return redirect(url_for('index'))

        # 必須入力チェック
        manager = form.get('manager', '').strip()
        comment = form.get('comment', '').strip()
        approver = form.get('approver', '').strip()
        qty_checked = form.getlist('qty_checked')

        # ▼ 譲渡申請情報の取得
        with_transfer = form.get("with_transfer") == "1"
        transfer_branch_ids = form.getlist("transfer_branch_ids") if with_transfer else []
        transfer_comment = form.get("transfer_comment", "") if with_transfer else ""
        transfer_date = form.get("transfer_date", "") if with_transfer else ""

        # 管理者サジェスト正当性チェック
        manager = form.get('manager', '').strip()
        errors = []
        if not manager or manager not in proper_usernames:
            errors.append("管理者は候補から選択してください。")
        if not approver:
            errors.append("承認者を選択してください。")
        if len(qty_checked) != len(item_ids):
            errors.append("すべての数量チェックをしてください。")
        if with_transfer:
            if not transfer_branch_ids:
                errors.append("譲渡する枝番を選択してください。")
            if not transfer_comment.strip():
                errors.append("譲渡コメントを入力してください。")

        if errors:
            items = db.execute(
                f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
            ).fetchall()
            items = [dict(row) for row in items]

            # --- 再描画時も利用可能枝番を付与＆0件 item を除外 ---
            from collections import defaultdict
            placeholders = ','.join(['?'] * len(item_ids))
            child_rows = db.execute(
                f"SELECT item_id, branch_no, status FROM child_item WHERE item_id IN ({placeholders})",
                item_ids
            ).fetchall()

            eligible = defaultdict(list)
            for r in child_rows:
                if r['status'] not in ('破棄', '譲渡'):
                    eligible[r['item_id']].append(r['branch_no'])

            for it in items:
                fallback_n = int(it.get('num_of_samples') or 1)
                fallback_branches = list(range(1, fallback_n + 1))
                branches = eligible.get(it['id'], fallback_branches)
                it['available_branches'] = sorted(branches)
                it['available_count'] = len(branches)

            items = [it for it in items if it['available_count'] > 0]
            department = g.user['department']
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''
            error_dialog_message = " ".join(errors)
            return render_template(
                'checkout_form.html',
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
        if with_transfer:
            new_status = "持ち出し譲渡申請中"
        else:
            new_status = "持ち出し申請中"

        applicant = g.user['username']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_date = form.get('start_date', '')
        end_date = form.get('end_date', '')

        owner_lists = {}
        for id in item_ids:
            owner_lists[str(id)] = form.getlist(f'owner_list_{id}')

        for id in item_ids:
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            original_status = item['status']

            db.execute("UPDATE item SET status=? WHERE id=?", (new_status, id))

            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            new_values = dict(item)
            new_values['sample_manager'] = manager
            if with_transfer:
                new_values['status'] = "持ち出し譲渡申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])
                new_values['transfer_date'] = transfer_date
                transfer_ids_this = []
                for t in transfer_branch_ids:
                    try:
                        tid, branch_no = t.split("_")
                        if str(tid) == str(id):
                            transfer_ids_this.append(int(branch_no))
                    except Exception:
                        continue
                new_values['transfer_branch_nos'] = transfer_ids_this
                new_values['transfer_comment'] = transfer_comment
            else:
                new_values['status'] = "持ち出し申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id, json.dumps(new_values, ensure_ascii=False), applicant, comment, approver, "申請中", now_str, original_status
            ))

        db.commit()

        to=""
        subject=""
        body=""
        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"持ち出し申請を保存しました。承認待ちです。承認者にメールで連絡しました。")
        else:
            flash(f"持ち出し申請を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        if request.form.get('from_menu') or request.args.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index'))

    return redirect(url_for('index'))


@app.route('/return_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def return_request():
    db = get_db()
    # 申請フォーム表示（POST:選択済みID受取→フォーム表示）
    if request.method == 'POST':
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index'))
        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        not_accepted = [str(row['id']) for row in items if row['status'] != "持ち出し中"]
        if not_accepted:
            flash(f"持ち出し中でないアイテム（ID: {', '.join(not_accepted)}）は持ち出し終了申請できません。")
            return redirect(url_for('index'))

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
            approver_list=sorted_managers
        )

    # 申請フォームからの送信時（GET, action=submit）
    if request.args.get('action') == 'submit':
        item_ids = request.args.getlist('item_id')
        checkeds = request.args.getlist('qty_checked')
        if not checkeds or len(checkeds) != len(item_ids):
            flash("全ての数量チェックを確認してください")
            return redirect(url_for('index'))

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

        to=""
        subject=""
        body=""
        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"申請内容を保存しました。承認待ちです。承認者にメールで連絡しました。")
        else:
            flash(f"申請内容を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        if request.args.get('from_menu') or request.form.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index'))
    
    return redirect(url_for('index'))


@app.route('/approval', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager')
def approval():
    db = get_db()
    username = g.user['username']

    # 承認対象リスト取得 & 申請内容をパースして渡す
    items_raw = db.execute(
        "SELECT * FROM item_application WHERE approver=? AND status=? ORDER BY application_datetime DESC",
        (username, "申請中")
    ).fetchall()
    parsed_items = []
    for item in items_raw:
        parsed = dict(item)
        try:
            parsed['parsed_values'] = json.loads(item['new_values'])
        except Exception:
            parsed['parsed_values'] = {}

        # ===== 所有者リストの枝番を「破棄・譲渡をスキップ」して表示用に再割当 =====
        pv = parsed.get('parsed_values', {})
        owners = pv.get('owner_list') or []
        if owners:
            item_id = parsed['item_id']

            # 既存 child_item の枝番状況を取得
            rows = db.execute(
                "SELECT branch_no, status FROM child_item WHERE item_id=?",
                (item_id,)
            ).fetchall()
            # 破棄・譲渡の枝番は使用不可
            disposed_transferred = {r['branch_no'] for r in rows if r['status'] in ('破棄', '譲渡')}
            occupied_alive = {r['branch_no'] for r in rows if r['status'] not in ('破棄', '譲渡')}

            # 空いている「生きている枝番」を小さい順に再利用し、足りなければ新規の枝番番号を継ぎ足し
            owner_pairs = []  # [(branch_no, owner), ...]
            # まず再利用候補（生きている既存枝番）を昇順で
            reuse_candidates = sorted(occupied_alive)
            reuse_iter = iter(reuse_candidates)

            # 次に新規採番開始位置（既存最大枝番の次）
            max_existing = max([0] + [r['branch_no'] for r in rows])
            next_branch = max_existing + 1

            for owner in owners:
                # 破棄・譲渡を避けて既存の生き枝番を優先
                try:
                    b = next(reuse_iter)
                except StopIteration:
                    # 足りなければ disposed/transferred を避けつつ新規採番
                    while next_branch in disposed_transferred:
                        next_branch += 1
                    b = next_branch
                    next_branch += 1

                owner_pairs.append((b, owner))

            parsed['owner_pairs'] = owner_pairs  # テンプレで使う
        else:
            parsed['owner_pairs'] = []

        parsed_items.append(parsed)
        
    items = parsed_items

    if request.method == 'POST':
        selected_ids = request.form.getlist('selected_ids')
        comment = request.form.get('approve_comment', '').strip()
        action = request.form.get('action')
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        now_date = datetime.now().strftime("%Y-%m-%d")

        if not selected_ids:
            flash("対象を選択してください")
            # 再度リスト生成
            items_raw = db.execute(
                "SELECT * FROM item_application WHERE approver=? AND status=? ORDER BY application_datetime DESC",
                (username, "申請中")
            ).fetchall()
            parsed_items = []
            for item in items_raw:
                parsed = dict(item)
                try:
                    parsed['parsed_values'] = json.loads(item['new_values'])
                except Exception:
                    parsed['parsed_values'] = {}
                parsed_items.append(parsed)
            return render_template('approval.html', items=parsed_items, fields=INDEX_FIELDS)

        for app_id in selected_ids:
            app_row = db.execute("SELECT * FROM item_application WHERE id=?", (app_id,)).fetchone()
            if not app_row:
                continue
            item_id = app_row['item_id']
            new_values = json.loads(app_row['new_values'])
            status = new_values.get('status')

            if action == 'approve':
                item_columns = set(FIELD_KEYS)
                filtered_values = {k: v for k, v in new_values.items() if k in item_columns}
                set_clause = ", ".join([f"{k}=?" for k in filtered_values.keys()])
                params = [filtered_values[k] for k in filtered_values.keys()]
                if set_clause:
                    db.execute(
                        f'UPDATE item SET {set_clause} WHERE id=?',
                        params + [item_id]
                    )

                approver_dept = (g.user['department'] or "").strip()
                if status in ("入庫持ち出し譲渡申請中", "入庫持ち出し申請中", "入庫申請中"):
                    db.execute(
                        "UPDATE item SET approval_group=? WHERE id=?",
                        (approver_dept, item_id)
                    )

                if status == "入庫持ち出し譲渡申請中":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))

                    start_date = new_values.get("checkout_start_date", "")
                    end_date = new_values.get("checkout_end_date", "")
                    owners = new_values.get("owner_list", [])
                    num_of_samples = int(new_values.get("num_of_samples", 1))
                    if not owners:
                        owner = new_values.get("sample_manager", "")
                        owners = [owner] * num_of_samples

                    # child_itemの作成または更新（ownerとstatusだけ）
                    for idx, owner in enumerate(owners, 1):
                        child_item = db.execute(
                            "SELECT * FROM child_item WHERE item_id=? AND branch_no=?", (item_id, idx)
                        ).fetchone()
                        if not child_item:
                            db.execute(
                                "INSERT INTO child_item (item_id, branch_no, owner, status, comment) VALUES (?, ?, ?, ?, ?)",
                                (item_id, idx, owner, "持ち出し中", "")
                            )
                            child_item_id = db.execute(
                                "SELECT id FROM child_item WHERE item_id=? AND branch_no=?", (item_id, idx)
                            ).fetchone()["id"]
                        else:
                            child_item_id = child_item["id"]
                            db.execute(
                                "UPDATE child_item SET owner=?, status=? WHERE id=?",
                                (owner, "持ち出し中", child_item_id)
                            )

                    # checkout_history に履歴を追加
                    db.execute(
                        "INSERT INTO checkout_history (item_id, checkout_start_date, checkout_end_date) VALUES (?, ?, ?)",
                        (item_id, start_date, end_date)
                    )

                    # 指定枝番(branch_no)を譲渡済みに変更（ownerも空欄に）
                    transfer_branch_nos = new_values.get("transfer_branch_nos", [])
                    transfer_date = new_values.get("transfer_date", "")
                    transfer_comment = new_values.get("transfer_comment", "")
                    for branch_no in transfer_branch_nos:
                        db.execute(
                            "UPDATE child_item SET status=?, comment=?, owner=?, transfer_dispose_date=? WHERE item_id=? AND branch_no=?",
                            ("譲渡", transfer_comment, '', transfer_date, item_id, branch_no)
                        )

                elif status == "入庫持ち出し申請中":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))
                    start_date = new_values.get("checkout_start_date", "")
                    end_date = new_values.get("checkout_end_date", "")
                    owners = new_values.get("owner_list", [])
                    if not owners:
                        num_of_samples = int(new_values.get("num_of_samples", 1))
                        owner = new_values.get("sample_manager", "")
                        owners = [owner] * num_of_samples
                    for idx, owner in enumerate(owners, 1):
                        child_item = db.execute(
                            "SELECT * FROM child_item WHERE item_id=? AND branch_no=?", (item_id, idx)
                        ).fetchone()
                        if not child_item:
                            db.execute(
                                "INSERT INTO child_item (item_id, branch_no, owner, status, comment) VALUES (?, ?, ?, ?, ?)",
                                (item_id, idx, owner, "持ち出し中", "")
                            )
                            child_item_id = db.execute(
                                "SELECT id FROM child_item WHERE item_id=? AND branch_no=?", (item_id, idx)
                            ).fetchone()["id"]
                        else:
                            child_item_id = child_item["id"]
                            db.execute(
                                "UPDATE child_item SET owner=?, status=? WHERE id=?",
                                (owner, "持ち出し中", child_item_id)
                            )

                    # checkout_history に履歴を追加
                    db.execute(
                        "INSERT INTO checkout_history (item_id, checkout_start_date, checkout_end_date) VALUES (?, ?, ?)",
                        (item_id, start_date, end_date)
                    )

                elif status == "入庫申請中":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("入庫", item_id))

                elif status in ("持ち出し申請中", "持ち出し譲渡申請中"):
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))
                    start_date = new_values.get("checkout_start_date", "")
                    end_date = new_values.get("checkout_end_date", "")

                    owners = new_values.get("owner_list", [])
                    if not owners:
                        num_of_samples = int(new_values.get("num_of_samples", 1))
                        owner_fallback = new_values.get("sample_manager", "")
                        owners = [owner_fallback] * num_of_samples

                    # 既存の child_item 取得
                    rows = db.execute(
                        "SELECT branch_no, status FROM child_item WHERE item_id=? ORDER BY branch_no",
                        (item_id,)
                    ).fetchall()

                    if not rows:
                        # 0レコードなら新規作成（1..N）
                        for idx, owner in enumerate(owners, 1):
                            db.execute(
                                "INSERT INTO child_item (item_id, branch_no, owner, status, comment) VALUES (?, ?, ?, ?, ?)",
                                (item_id, idx, owner, "持ち出し中", "")
                            )
                    else:
                        # 1件でもあれば追加しない（UPDATEのみ）
                        alive = [r["branch_no"] for r in rows if r["status"] not in ("破棄","譲渡")]

                        # 生き枝番が owners より少なければ、この申請は不整合なのでスキップ（全体は継続）
                        if len(owners) > len(alive):
                            flash(f"通し番号 {item_id}: 生きている枝番（{len(alive)}）より所有者が多い（{len(owners)}）ため、追加は行わず更新できません。")
                            continue

                        # 小さい順に一度ずつ割り当てて UPDATE（破棄/譲渡は据え置き）
                        for branch_no, owner in zip(alive, owners):
                            db.execute(
                                """
                                UPDATE child_item
                                SET owner  = CASE WHEN status IN (?, ?) THEN owner ELSE ? END,
                                    status = CASE WHEN status IN (?, ?) THEN status ELSE ? END
                                WHERE item_id=? AND branch_no=?
                                """,
                                ("破棄","譲渡", owner, "破棄","譲渡", "持ち出し中", item_id, branch_no)
                            )
                        # ※ owners が alive より少ない場合、余った枝番は触らず据え置き

                    # 履歴
                    db.execute(
                        "INSERT INTO checkout_history (item_id, checkout_start_date, checkout_end_date) VALUES (?, ?, ?)",
                        (item_id, start_date, end_date)
                    )

                    # 譲渡申請時の譲渡済処理（ownerも空欄に）
                    if status == "持ち出し譲渡申請中":
                        transfer_branch_nos = new_values.get("transfer_branch_nos", [])
                        transfer_date = new_values.get("transfer_date", "")
                        transfer_comment = new_values.get("transfer_comment", "")
                        for branch_no in transfer_branch_nos:
                            db.execute(
                                "UPDATE child_item SET status=?, comment=?, owner=?, transfer_dispose_date=? WHERE item_id=? AND branch_no=?",
                                ("譲渡", transfer_comment, '', transfer_date, item_id, branch_no)
                            )

                elif status == "返却申請中":
                    db.execute(
                        "UPDATE item SET status=?, storage=? WHERE id=?",
                        ("入庫", new_values.get("storage", ""), item_id)
                    )
                    # 返却対象の child_item を「返却済」に（ownerも空欄に）
                    db.execute(
                        """
                        UPDATE child_item
                        SET status=?, owner=?
                        WHERE item_id=? AND status NOT IN (?, ?)
                        """,
                        ("返却済", '', item_id, "破棄", "譲渡")
                    )
                    # 履歴にも返却を追加する場合はこちらで処理（例: checkout_end_date記録など）

                elif status == "破棄・譲渡申請中":
                    dispose_type = new_values.get('dispose_type')
                    target_child_branches = new_values.get('target_child_branches', [])
                    transfer_dispose_date = new_values.get("dispose_date", "")
                    dispose_comment = new_values.get('dispose_comment', '')

                    new_status = "破棄" if dispose_type == "破棄" else "譲渡"
                    for target in target_child_branches:
                        cid = target["id"]
                        db.execute(
                            "UPDATE child_item SET status=?, comment=?, owner=?, transfer_dispose_date=?  WHERE id=?",
                            (new_status, dispose_comment, '', transfer_dispose_date, cid)  # ownerも空欄に
                        )
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))

                # item_applicationのstatusを承認に
                db.execute('''
                    UPDATE item_application SET
                        approver_comment=?, approval_datetime=?, status=?
                    WHERE id=?
                ''', (comment, now_str, "承認", app_id))

                # application_historyにも履歴を登録
                db.execute('''
                    INSERT INTO application_history
                    (item_id, applicant, application_content, applicant_comment, application_datetime, approver, status, approval_datetime, approver_comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    item_id, app_row['applicant'],
                    "入庫持ち出し譲渡申請" if status == "入庫持ち出し譲渡申請中"
                    else "持ち出し譲渡申請" if status == "持ち出し譲渡申請中"
                    else "破棄申請" if status == "破棄・譲渡申請中" and new_values.get("dispose_type") == "破棄"
                    else "譲渡申請" if status == "破棄・譲渡申請中" and new_values.get("dispose_type") == "譲渡"
                    else "入庫申請" if status == "入庫申請中"
                    else "入庫持ち出し申請" if status == "入庫持ち出し申請中"
                    else "持ち出し申請" if status == "持ち出し申請中"
                    else "持ち出し終了申請" if status == "返却申請中"
                    else (app_row['new_values'] or status or ""),
                    app_row['applicant_comment'], app_row['application_datetime'], app_row['approver'],
                    "承認", now_str, comment
                ))

                # メール通知
                to=""
                subject=""
                body=""
                result = send_mail(to=to, subject=subject, body=body)
                if result:
                    flash(f"承認しました。申請者にメールで連絡しました。")
                else:
                    flash(f"承認しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

            elif action == 'reject':
                original_status = app_row['original_status']
                if original_status:
                    db.execute("UPDATE item SET status=? WHERE id=?", (original_status, item_id))
                db.execute(
                    '''
                    UPDATE item_application SET
                        approver_comment=?, approval_datetime=?, status=?
                    WHERE id=?
                    ''',
                    (comment, now_str, "差し戻し", app_id)
                )

                # メール通知
                to=""
                subject=""
                body=""
                result = send_mail(to=to, subject=subject, body=body)
                if result:
                    flash(f"差し戻しました。申請者にメールで連絡しました。")
                else:
                    flash(f"差し戻しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        db.commit()
        # 完了後は空リスト＋メッセージ表示
        return render_template('approval.html', items=[], fields=INDEX_FIELDS, message="処理が完了しました", finish=True)

    return render_template('approval.html', items=items, fields=INDEX_FIELDS)


@app.route('/child_items')
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def child_items_multiple():
    ids_str = request.args.get('ids', '')
    if not ids_str:
        flash("通し番号が指定されていません")
        return redirect(url_for('index'))
    try:
        id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]
    except Exception:
        flash("不正なID指定")
        return redirect(url_for('index'))
    if not id_list:
        flash("通し番号が指定されていません")
        return redirect(url_for('index'))

    db = get_db()

    # 子アイテム
    q = f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(id_list))}) ORDER BY item_id, branch_no"
    child_items = db.execute(q, id_list).fetchall()

    # 親アイテム（概要表示 & 名称マップ）
    items_rows = db.execute(
        f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))}) ORDER BY id",
        id_list
    ).fetchall()
    items = [dict(r) for r in items_rows]
    item_map = {r['id']: dict(r) for r in items_rows}

    # ===== サンプル数を動的算出（破棄・譲渡を除外） =====
    # 一括集計で child_total と alive_cnt を取得
    agg_sql = f"""
        SELECT
            item_id,
            COUNT(*) AS child_total,
            SUM(CASE WHEN status NOT IN (?, ?) THEN 1 ELSE 0 END) AS alive_cnt
        FROM child_item
        WHERE item_id IN ({','.join(['?']*len(id_list))})
        GROUP BY item_id
    """
    agg_params = ("破棄", "譲渡", *id_list)
    agg_rows = db.execute(agg_sql, agg_params).fetchall()
    agg_map = {row["item_id"]: {"child_total": row["child_total"], "alive_cnt": row["alive_cnt"]} for row in agg_rows}

    # 各親アイテムに sample_count を付与
    for it in items:
        it_id = it["id"]
        stats = agg_map.get(it_id)
        if not stats or stats["child_total"] == 0:
            # 子が無い場合は num_of_samples をそのまま利用（index と同じフォールバック）
            it["sample_count"] = it.get("num_of_samples", 0)
        else:
            it["sample_count"] = stats["alive_cnt"] or 0

    # ▼ 持ち出し申請履歴：checkout_history をそのまま表示用に整形
    hist_rows = db.execute(
        f"""
        SELECT item_id, checkout_start_date, checkout_end_date
        FROM checkout_history
        WHERE item_id IN ({','.join(['?']*len(id_list))})
        ORDER BY item_id, checkout_start_date DESC, checkout_end_date DESC
        """,
        id_list
    ).fetchall()

    checkout_histories = []
    for r in hist_rows:
        checkout_histories.append({
            'item_id': r['item_id'],
            'product_name': item_map.get(r['item_id'], {}).get('product_name', ''),
            'checkout_start_date': r['checkout_start_date'],
            'checkout_end_date': r['checkout_end_date'],
        })

    return render_template(
        'child_items.html',
        child_items=child_items,
        item_map=item_map,
        items=items,
        fields=INDEX_FIELDS,
        checkout_histories=checkout_histories
    )


@app.route('/bulk_manager_change', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def bulk_manager_change():
    ids_str = request.args.get('ids') if request.method == 'GET' else request.form.get('ids')
    if not ids_str:
        flash("通し番号が指定されていません")
        return redirect(url_for('index'))

    id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]
    db = get_db()
    items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
    proper_users = get_proper_users(db)
    proper_usernames = [u['username'] for u in proper_users]
    proper_users_json = [
        {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        } for u in proper_users
    ]

    if request.method == 'POST':
        new_manager = request.form.get('new_manager', '').strip()
        if not new_manager or new_manager not in proper_usernames:
            # ダイアログでエラー表示
            items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
            return render_template(
                'bulk_manager_change.html',
                items=items, ids=ids_str,
                proper_users=proper_users_json,
                error_message="管理者は候補から選択してください。"
            )
        # 一括更新
        db.executemany("UPDATE item SET sample_manager=? WHERE id=?", [(new_manager, item['id']) for item in items])
        db.commit()

        # メール通知
        old_managers = set(item['sample_manager'] for item in items)
        to=""
        subject=""
        body=""
        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"管理者を「{new_manager}」に一括変更しました。旧管理者・新管理者・承認者にメールで連絡しました。")
        else:
            flash(f"管理者を「{new_manager}」に一括変更しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        if request.form.get('from_menu') or request.args.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index'))
        
    manager_default = g.user['username']
    return render_template(
        'bulk_manager_change.html',
        items=items, ids=ids_str,
        proper_users=proper_users_json,
        manager_default=manager_default,
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
    return render_template(
        'my_applications.html',
        applications=apps,
        status=status
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
        return redirect(url_for('index'))
    id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]

    # 対象item（持ち出し中のみ）
    items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
    target_ids = [item['id'] for item in items if item['status'] == "持ち出し中"]
    if not target_ids:
        flash("選択した中に所有者変更できるアイテムがありません（持ち出し中のみ可能）")
        return redirect(url_for('index'))

    # 子アイテム取得（枝番順）
    child_items = db.execute(
        f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(target_ids))}) ORDER BY item_id, branch_no",
        target_ids
    ).fetchall()

    # 編集画面
    if request.method == 'GET':
        return render_template(
            'change_owner.html',
            items=items,
            child_items=child_items,
            ids=','.join(str(i) for i in target_ids),
            owner_candidates=owner_candidates,
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
        to=""
        subject=""
        body=""
        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"所有者を変更しました。所有者・管理者・承認者にメールで連絡しました。")
        else:
            flash(f"所有者を変更しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

    else:
        flash("変更はありませんでした。")
    return redirect(url_for('index'))


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
                handler_default=handler_default
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

        to=""
        subject=""
        body=""
        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"破棄・譲渡申請を保存しました。承認待ちです。承認者にメールで連絡しました。")
        else:
            flash(f"破棄・譲渡申請を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        if request.form.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index'))

    # 申請画面表示（POST/GET共通: item_idリストで遷移）
    if request.method == 'POST':
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index'))
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
            handler_default=handler_default
        )

    return redirect(url_for('index'))


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

    return render_template('inventory_list.html', items=items, fields=INDEX_FIELDS)


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
    rows = db.execute(
        "SELECT * FROM inventory_check WHERE item_id=? ORDER BY checked_at DESC",
        (item_id,)
    ).fetchall()
    item = db.execute(
        "SELECT * FROM item WHERE id=?", (item_id,)
    ).fetchone()
    return render_template('inventory_history.html', rows=rows, item=item)


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
        return redirect(url_for('index'))

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
    """
    ids_raw = request.args.get('ids', '')
    ids = [s for s in ids_raw.split(',') if s.strip()]
    if not ids:
        flash("編集対象の通し番号を選択してください。")
        return redirect(url_for('index'))

    db = get_db()

    ok, blocked = acquire_locks(db, ids, g.user['username'])
    if not ok:
        flash("以下のIDは他ユーザーが編集中のため編集できません: " + ", ".join(blocked))
        return redirect(url_for('index'))

    # 編集対象の取得（index と同じ項目セット）
    user_field_keys = [f['key'] for f in FIELDS if not f.get('internal', False) and f.get('show_in_index', True)]
    # ID/サンプル数も index と同様に表示するため全列を取得
    placeholders = ",".join(["?"]*len(ids))
    rows = db.execute(f"SELECT * FROM item WHERE id IN ({placeholders}) ORDER BY id DESC", ids).fetchall()
    items = [dict(r) for r in rows]

    return render_template(
        'edit.html',
        items=items,
        fields=[f for f in FIELDS if f.get('show_in_index', True)],  # index と同じ表示項目
        user_field_keys=user_field_keys,
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
    return redirect(url_for('index'))


@app.route('/bulk_edit/cancel', methods=['POST'])
@login_required
def bulk_edit_cancel():
    db = get_db()
    ids = request.form.getlist('item_id')

    # 値は保存せず、ロックのみ解除（statusは触らない）
    release_locks(db, [int(x) for x in ids])
    flash("編集をキャンセルし、ロックを解除しました。")
    return redirect(url_for('index'))


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
    return redirect(url_for('index'))


if __name__ == '__main__':
    init_db()
    init_user_db()
    init_child_item_db()
    init_checkout_history_db()
    init_item_application_db()
    init_application_history_db()
    init_inventory_check_db()
    app.run(debug=True, host='0.0.0.0', port=8000)
