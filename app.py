import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json
from datetime import datetime


app = Flask(__name__)
app.secret_key = "any_secret"
DATABASE = 'items.db'

# フィールド定義（fields.jsonを利用）
FIELDS_PATH = os.path.join(os.path.dirname(__file__), 'fields.json')
with open(FIELDS_PATH, encoding='utf-8') as f:
    FIELDS = json.load(f)
USER_FIELDS = [f for f in FIELDS if not f.get('internal')]
INDEX_FIELDS = [f for f in FIELDS if f.get('show_in_index')]
FIELD_KEYS = [f['key'] for f in FIELDS]

SELECT_FIELD_PATH = os.path.join(os.path.dirname(__file__), 'select_fields.json')

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
                password TEXT NOT NULL,
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
                checkout_start_date TEXT,
                checkout_end_date TEXT,
                comment TEXT,
                UNIQUE(item_id, branch_no)
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
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(url_for('menu'))
        else:
            flash("ユーザー名またはパスワードが違います")
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
    部門指定で manager 権限ユーザー一覧を返す。
    department=None で全manager返す
    戻り値: [{'username': ..., 'realname': ..., 'department': ...}, ...]
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
    query += " ORDER BY u.username"
    rows = db.execute(query, params).fetchall()
    return [row['username'] for row in rows]


@app.route('/register', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager')
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
            return redirect(url_for('login'))

    return render_template('register.html', roles=roles)


@app.route('/')
@login_required
def menu():
    return render_template('menu.html')


@app.route('/list')
@login_required
def index():
    # ページ件数指定: ?per_page=10/20/50/100/all
    per_page_raw = request.args.get('per_page', '10')
    if per_page_raw == 'all':
        per_page = None
        page = 1
        offset = 0
    else:
        per_page = int(per_page_raw)
        page = int(request.args.get('page', 1))
        offset = (page - 1) * per_page

    filters = {}
    where = []
    params = []
    for f in INDEX_FIELDS:
        v = request.args.get(f"{f['key']}_filter", '').strip()
        filters[f['key']] = v
        if v:
            where.append(f"{f['key']} LIKE ?")
            params.append(f"%{v}%")
    sample_count_filter = request.args.get("sample_count_filter", "").strip()
    filters['sample_count'] = sample_count_filter
    where_clause = "WHERE " + " AND ".join(where) if where else ""
    db = get_db()

    # 通常/サンプル数フィルタによる分岐
    if not sample_count_filter:
        total = db.execute(f"SELECT COUNT(*) FROM item {where_clause}", params).fetchone()[0]
        query = f"SELECT * FROM item {where_clause} ORDER BY id DESC"
        if per_page is not None:
            query += " LIMIT ? OFFSET ?"
            items = db.execute(query, params + [per_page, offset]).fetchall()
        else:
            items = db.execute(query, params).fetchall()
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
    else:
        # サンプル数フィルタ時はPython側でフィルタ
        rows = db.execute(
            f"SELECT * FROM item {where_clause} ORDER BY id DESC",
            params
        ).fetchall()
        items_all = []
        for item in rows:
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
            items_all.append(item)
        items_filtered = [item for item in items_all if str(item['sample_count']) == sample_count_filter]
        total = len(items_filtered)
        if per_page is not None:
            item_list = items_filtered[offset:offset+per_page]
        else:
            item_list = items_filtered

    # フィルタ用候補（辞書）
    filter_choices_dict = {}
    for f in INDEX_FIELDS:
        col = f['key']
        rows = db.execute(f"SELECT DISTINCT {col} FROM item WHERE {col} IS NOT NULL AND {col} != ''").fetchall()
        filter_choices_dict[col] = sorted({str(row[col]) for row in rows if row[col] not in (None, '')})
    filter_choices_dict['sample_count'] = sorted({str(item['sample_count']) for item in item_list})

    # ページ数
    if per_page is not None:
        page_count = max(1, (total + per_page - 1) // per_page)
    else:
        page_count = 1

    return render_template(
        'index.html',
        items=item_list, page=page, page_count=page_count,
        filters=filters, total=total, fields=INDEX_FIELDS,
        filter_choices_dict=filter_choices_dict,
        per_page=per_page_raw,
    )


@app.route('/add', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def add():
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
            return render_template('form.html', fields=USER_FIELDS, values=user_values, select_fields=select_fields)

        db = get_db()
        db.execute(
            f'INSERT INTO item ({",".join(FIELD_KEYS)}) VALUES ({",".join(["?"]*len(FIELD_KEYS))})',
            values
        )
        db.commit()

        if 'add_and_next' in request.form:
            return render_template('form.html', fields=USER_FIELDS, values=user_values, select_fields=select_fields, message="登録しました。同じ内容で新規入力できます。")
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
    return render_template('form.html', fields=USER_FIELDS, values=values, select_fields=select_fields)


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

@app.route('/update_items', methods=['POST'])
@login_required
def update_items():
    db = get_db()
    ids = request.form.getlist('item_id')
    user_field_keys = [f['key'] for f in FIELDS if not f.get('internal', False) and f.get('show_in_index', True)]
    for item_id in ids:
        row_values = []
        for key in user_field_keys:
            row_values.append(request.form.get(f"{key}_{item_id}", ""))
        set_clause = ", ".join([f"{key}=?" for key in user_field_keys])
        db.execute(
            f'UPDATE item SET {set_clause} WHERE id=?',
            row_values + [item_id]
        )
    db.commit()
    if request.form.get('from_menu') or request.args.get('from_menu'):
        return redirect(url_for('menu'))
    else:
        return redirect(url_for('index'))


@app.route('/apply_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def apply_request():
    db = get_db()

    # 申請画面の表示（POST:選択済みID受取→フォーム表示）
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
        managers_same_dept = get_managers_by_department(department, db)
        all_managers = get_managers_by_department(None, db)
        return render_template(
            'apply_form.html',
            items=items, fields=INDEX_FIELDS,
            approver_default=managers_same_dept[0] if managers_same_dept else '',
            approver_list=all_managers
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

        errors = []
        if not manager:
            errors.append("管理者を入力してください。")
        if not approver:
            errors.append("承認者を入力してください。")
        if len(qty_checked) != len(item_ids):
            errors.append("すべての数量チェックをしてください。")
        # ▼ 新規：譲渡申請ONなら枝番必須
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
            for msg in errors:
                flash(msg)
            department = g.user['department']
            managers_same_dept = get_managers_by_department(department, db)
            all_managers = get_managers_by_department(None, db)
            return render_template(
                'apply_form.html',
                items=items, fields=INDEX_FIELDS,
                approver_default=managers_same_dept[0] if managers_same_dept else '',
                approver_list=all_managers
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
        flash("申請内容を保存しました。承認待ちです。")
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
        # ステータスチェック
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
        managers_same_dept = get_managers_by_department(department, db)
        all_managers = get_managers_by_department(None, db)
        return render_template(
            'return_form.html',
            items=item_list, fields=INDEX_FIELDS,
            approver_default=managers_same_dept[0] if managers_same_dept else '',
            approver_list=all_managers
        )

    # 申請フォームからの送信時（GET, action=submit）: item_applicationへ申請内容登録
    if request.args.get('action') == 'submit':
        item_ids = request.args.getlist('item_id')
        checkeds = request.args.getlist('qty_checked')
        if not checkeds or len(checkeds) != len(item_ids):
            flash("全ての数量チェックを確認してください")
            return redirect(url_for('index'))

        # 申請内容
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
        flash("申請内容を保存しました。承認待ちです。")
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

                if status == "入庫持ち出し譲渡申請中":
                    # 1. itemテーブル: ステータス"入庫"→"持ち出し中"（可読性のため2段階でもOK。ここでは一気に"持ち出し中"に）
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))

                    start_date = new_values.get("checkout_start_date", "")
                    end_date = new_values.get("checkout_end_date", "")
                    owners = new_values.get("owner_list", [])
                    num_of_samples = int(new_values.get("num_of_samples", 1))
                    if not owners:
                        owner = new_values.get("sample_manager", "")
                        owners = [owner] * num_of_samples

                    # 2. child_item（持ち出し分）を「持ち出し中」で作成または更新
                    for idx, owner in enumerate(owners, 1):
                        db.execute(
                            '''
                            INSERT INTO child_item
                                (item_id, branch_no, owner, status, checkout_start_date, checkout_end_date)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(item_id, branch_no)
                            DO UPDATE SET
                                owner=excluded.owner,
                                status=excluded.status,
                                checkout_start_date=excluded.checkout_start_date,
                                checkout_end_date=excluded.checkout_end_date
                            ''',
                            (item_id, idx, owner, "持ち出し中", start_date, end_date)
                        )

                    # 3. 指定枝番(branch_no)を譲渡済みに変更（ここを修正！）
                    transfer_branch_nos = new_values.get("transfer_branch_nos", [])
                    transfer_comment = new_values.get("transfer_comment", "")
                    for branch_no in transfer_branch_nos:
                        db.execute(
                            "UPDATE child_item SET status=?, comment=?, checkout_end_date=?, owner=? WHERE item_id=? AND branch_no=?",
                            ("譲渡", transfer_comment, now_date, '', item_id, branch_no)
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
                        db.execute(
                            '''
                            INSERT INTO child_item
                                (item_id, branch_no, owner, status, checkout_start_date, checkout_end_date)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(item_id, branch_no)
                            DO UPDATE SET
                                owner=excluded.owner,
                                status=excluded.status,
                                checkout_start_date=excluded.checkout_start_date,
                                checkout_end_date=excluded.checkout_end_date
                            ''',
                            (item_id, idx, owner, "持ち出し中", start_date, end_date)
                        )
                elif status == "入庫申請中":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("入庫", item_id))
                elif status == "返却申請中":
                    db.execute(
                        "UPDATE item SET status=?, storage=? WHERE id=?",
                        ("入庫", new_values.get("storage", ""), item_id)
                    )
                    db.execute(
                        """
                        UPDATE child_item
                        SET status=?, checkout_end_date=?
                        WHERE item_id=? AND status NOT IN (?, ?)
                        """,
                        ("返却済", new_values.get("return_date", ""), item_id, "破棄", "譲渡")
                    )
                elif status == "破棄・譲渡申請中":
                    dispose_type = new_values.get('dispose_type')
                    target_child_branches = new_values.get('target_child_branches', [])
                    dispose_comment = new_values.get('dispose_comment', '')

                    new_status = "破棄" if dispose_type == "破棄" else "譲渡"
                    for target in target_child_branches:
                        cid = target["id"]
                        db.execute(
                            "UPDATE child_item SET status=?, comment=?, checkout_end_date=?, owner=? WHERE id=?",
                            (new_status, dispose_comment, now_date, '', cid)
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
                    else "破棄申請" if status == "破棄・譲渡申請中" and new_values.get("dispose_type") == "破棄"
                    else "譲渡申請" if status == "破棄・譲渡申請中" and new_values.get("dispose_type") == "譲渡"
                    else "入庫申請" if status == "入庫申請中"
                    else "入庫持ち出し申請" if status == "入庫持ち出し申請中"
                    else "持ち出し終了申請" if status == "返却申請中"
                    else (app_row['new_values'] or status or ""),
                    app_row['applicant_comment'], app_row['application_datetime'], app_row['approver'],
                    "承認", now_str, comment
                ))

            elif action == 'reject':
                # original_statusは必ず申請テーブルに入っている前提
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
                # 必要に応じて履歴登録など

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
    # 子アイテム取得
    q = f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(id_list))}) ORDER BY item_id, branch_no"
    child_items = db.execute(q, id_list).fetchall()
    # 親アイテムも必要なら（例：item名の付与用）
    items = db.execute(f"SELECT id, product_name FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
    item_map = {i['id']: i for i in items}
    return render_template('child_items.html', child_items=child_items, item_map=item_map)

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
    
    if request.method == 'POST':
        new_manager = request.form.get('new_manager', '').strip()
        if not new_manager:
            flash("新しい管理者名を入力してください")
            return render_template('bulk_manager_change.html', items=items, ids=ids_str)
        # 一括更新
        db.executemany("UPDATE item SET sample_manager=? WHERE id=?", [(new_manager, item['id']) for item in items])
        db.commit()
        # 仮メール通知（ダイアログ表示のみ）
        old_managers = set(item['sample_manager'] for item in items)
        # ここで実際はメール送信処理
        flash(f"管理者を「{new_manager}」に一括変更しました。旧管理者・新管理者・承認者にメールで連絡しました（ダイアログ仮表示）。")
        if request.form.get('from_menu') or request.args.get('from_menu'):
            return redirect(url_for('menu'))
        else:
            return redirect(url_for('index'))
    return render_template('bulk_manager_change.html', items=items, ids=ids_str)

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
            ids=','.join(str(i) for i in target_ids)
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
        flash("所有者を変更しました。管理者・責任者・自分にメール送信しました（仮実装）。")
    else:
        flash("変更はありませんでした。")
    return redirect(url_for('index'))


@app.route('/dispose_transfer_request', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper')
def dispose_transfer_request():
    db = get_db()
    # POST: 申請フォーム送信
    if request.method == 'POST' and request.form.get('action') == 'submit':
        item_ids = request.form.getlist('item_id')
        dispose_type = request.form.get('dispose_type', '')
        handler = request.form.get('handler', '').strip()
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
            errors.append("承認者を入力してください。")
        if not target_child_ids:
            errors.append("少なくとも1つの子アイテムを選択してください。")
        if len(qty_checked_ids) != len(item_ids):
            errors.append("すべての親アイテムで数量チェックをしてください。")

        if errors:
            # item/child_items両方にサンプル数計算を追加
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
            return render_template(
                'dispose_transfer_form.html',
                items=item_list, child_items=child_items, fields=INDEX_FIELDS,
                approver_default=managers_same_dept[0] if managers_same_dept else '',
                approver_list=all_managers
            )

        applicant = g.user['username']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item_id in item_ids:
            # 親ID（item）の全情報を取得＋サンプル数も埋め込む
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
            new_values['handler'] = handler
            new_values['dispose_comment'] = dispose_comment

            # このitem_idに紐づく申請対象子アイテム(branch_no付き)
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

        flash("破棄・譲渡申請を保存しました。承認待ちです。")
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
        # サンプル数計算
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
        return render_template(
            'dispose_transfer_form.html',
            items=item_list, child_items=child_items, fields=INDEX_FIELDS,
            approver_default=managers_same_dept[0] if managers_same_dept else '',
            approver_list=all_managers
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


if __name__ == '__main__':
    init_db()
    init_user_db()
    init_child_item_db()
    init_item_application_db()
    init_application_history_db()
    init_inventory_check_db()
    app.run(debug=True)
