import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json

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
                password TEXT NOT NULL
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
        for role in ["manager", "owner", "general"]:
            db.execute("INSERT OR IGNORE INTO roles (name) VALUES (?)", (role,))
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
                checkout_end_date TEXT
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
            return redirect(url_for('index'))
        else:
            flash("ユーザー名またはパスワードが違います")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    db = get_db()
    roles = db.execute("SELECT * FROM roles").fetchall()
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        selected_roles = request.form.getlist('roles')
        if not username or not password or not selected_roles:
            flash("すべて入力してください")
        elif db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone():
            flash("そのユーザー名は既に使われています")
        else:
            db.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, generate_password_hash(password))
            )
            user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']
            for role_id in selected_roles:
                db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
            db.commit()
            flash("登録完了。ログインしてください。")
            return redirect(url_for('login'))
    return render_template('register.html', roles=roles)

@app.route('/')
@login_required
def index():
    page = int(request.args.get('page', 1))
    per_page = 10
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
    where_clause = "WHERE " + " AND ".join(where) if where else ""
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM item {where_clause}", params).fetchone()[0]
    items = db.execute(
        f"SELECT * FROM item {where_clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()
    page_count = max(1, (total + per_page - 1) // per_page)
    return render_template(
        'index.html',
        items=items, page=page, page_count=page_count,
        filters=filters, total=total, fields=INDEX_FIELDS
    )

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        user_values = [request.form.get(f['key'], '').strip() for f in USER_FIELDS]
        errors = []
        for i, f in enumerate(USER_FIELDS):
            if f.get('required') and not user_values[i]:
                errors.append(f"{f['name']}（必須）を入力してください。")
        internal_values = []
        for f in FIELDS:
            if f.get('internal', False):
                if f['key'] == 'status':
                    internal_values.append("入庫前")
                else:
                    internal_values.append("")
        values = user_values + internal_values

        if errors:
            for msg in errors:
                flash(msg)
            return render_template('form.html', fields=USER_FIELDS, values=user_values)

        db = get_db()
        db.execute(
            f'INSERT INTO item ({",".join(FIELD_KEYS)}) VALUES ({",".join(["?"]*len(FIELD_KEYS))})',
            values
        )
        db.commit()

        if 'add_and_next' in request.form:
            return render_template('form.html', fields=USER_FIELDS, values=user_values, message="登録しました。同じ内容で新規入力できます。")
        else:
            return redirect(url_for('index'))

    return render_template('form.html', fields=USER_FIELDS, values=["" for _ in USER_FIELDS])

@app.route('/delete_selected', methods=['POST'])
@login_required
def delete_selected():
    ids = request.form.getlist('selected_ids')
    if ids:
        db = get_db()
        db.executemany('DELETE FROM item WHERE id=?', [(item_id,) for item_id in ids])
        db.commit()
    return redirect(url_for('index'))

@app.route('/update_items', methods=['POST'])
@login_required
def update_items():
    db = get_db()
    ids = request.form.getlist('item_id')
    for item_id in ids:
        row_values = []
        for f in FIELDS:
            value = request.form.get(f"{f['key']}_{item_id}", "")
            row_values.append(value)
        set_clause = ", ".join([f"{f['key']}=?" for f in FIELDS])
        db.execute(
            f'UPDATE item SET {set_clause} WHERE id=?',
            row_values + [item_id]
        )
    db.commit()
    return redirect(url_for('index'))

@app.route('/apply_request', methods=['POST', 'GET'])
@login_required
def apply_request():
    if request.method == 'POST':
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index'))

        db = get_db()
        items = [dict(row) for row in db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        )]
        return render_template('apply_form.html', items=items, fields=INDEX_FIELDS)

    # 申請ボタン（GET）処理
    if request.args.get('action') == 'submit':
        item_ids = request.args.getlist('item_id')
        checkeds = request.args.getlist('qty_checked')
        if not checkeds or len(checkeds) != len(item_ids):
            flash("すべての数量チェックを確認してください")
            return redirect(url_for('index'))

        db = get_db()
        # 持ち出し申請の有無を確認
        with_checkout = request.args.get("with_checkout") == "1"
        new_status = "入庫持ち出し申請中" if with_checkout else "入庫申請中"
        for id in item_ids:
            db.execute("UPDATE item SET status=? WHERE id=?", (new_status, id))

        if with_checkout:
            # 申請フォームから日付取得
            start_date = request.args.get('start_date', '')
            end_date = request.args.get('end_date', '')
            for item_id in item_ids:
                owners = request.args.getlist(f"owner_list_{item_id}")
                for idx, owner in enumerate(owners, 1):
                    db.execute(
                        "INSERT INTO child_item (item_id, branch_no, owner, status, checkout_start_date, checkout_end_date) VALUES (?, ?, ?, ?, ?, ?)",
                        (item_id, idx, owner, "持ち出し申請中", start_date, end_date)
                    )

        db.commit()
        # ここで申請内容を再取得
        items = [dict(row) for row in db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        )]
        return render_template('apply_form.html', items=items, fields=INDEX_FIELDS, message="申請が完了しました（ダイアログで通知：本来はメール送信）", finish=True)

    return redirect(url_for('index'))

@app.route('/approval', methods=['GET', 'POST'])
@login_required
def approval():
    db = get_db()
    if request.method == 'POST':
        selected_ids = request.form.getlist('selected_ids')
        comment = request.form.get('reject_comment', '').strip()
        action = request.form.get('action')

        if not selected_ids:
            flash("対象を選択してください")
            items = db.execute("SELECT * FROM item WHERE status LIKE ?", ("%申請中%",)).fetchall()
            return render_template('approval.html', items=items, fields=INDEX_FIELDS)

        if action == 'approve':
            for item_id in selected_ids:
                db.execute("UPDATE item SET status=? WHERE id=?", ("入庫", item_id))
            db.commit()
            return render_template('approval.html', items=[], fields=INDEX_FIELDS, message="承認完了（申請者へメール送信ダイアログ）", finish=True)
        elif action == 'reject':
            for item_id in selected_ids:
                db.execute("UPDATE item SET status=? WHERE id=?", ("入庫差し戻し", item_id))
            db.commit()
            return render_template('approval.html', items=[], fields=INDEX_FIELDS, message=f"差し戻し完了: {comment}（申請者へメール送信ダイアログ）", finish=True)

    items = db.execute("SELECT * FROM item WHERE status LIKE ?", ("%申請中%",)).fetchall()
    return render_template('approval.html', items=items, fields=INDEX_FIELDS)

@app.route('/child_items')
@login_required
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

if __name__ == '__main__':
    init_db()
    init_user_db()
    init_child_item_db()
    app.run(debug=True)
