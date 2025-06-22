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
                checkout_end_date TEXT,
                UNIQUE(item_id, branch_no)
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
    user_field_keys = [f['key'] for f in FIELDS if not f.get('internal')]
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
        # 申請対象に「入庫前」以外が含まれていないかチェック
        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        not_accepted = [str(row['id']) for row in items if row['status'] != "入庫前"]
        if not_accepted:
            flash(f"入庫前でないアイテム（ID: {', '.join(not_accepted)}）は入庫申請できません。")
            return redirect(url_for('index'))

        items = [dict(row) for row in items]
        return render_template('apply_form.html', items=items, fields=INDEX_FIELDS)

    # 申請ボタン（GET）処理
    if request.args.get('action') == 'submit':
        item_ids = request.args.getlist('item_id')
        checkeds = request.args.getlist('qty_checked')
        if not checkeds or len(checkeds) != len(item_ids):
            flash("すべての数量チェックを確認してください")
            return redirect(url_for('index'))

        db = get_db()
        with_checkout = request.args.get("with_checkout") == "1"
        new_status = "入庫持ち出し申請中" if with_checkout else "入庫申請中"

        # コメント・ユーザー名・承認者取得
        applicant = g.user['username']
        applicant_comment = request.args.get('comment', '')
        approver = request.args.get('approver', '')
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for id in item_ids:
            # アイテムのステータスを更新
            db.execute("UPDATE item SET status=? WHERE id=?", (new_status, id))

            # 履歴：入庫申請
            db.execute('''
                INSERT INTO application_history
                    (item_id, applicant, application_content, applicant_comment, application_datetime, approver, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                id, applicant, "入庫申請", applicant_comment, now_str, approver, "申請中"
            ))

            if with_checkout:
                # 履歴：持ち出し申請（入庫申請と分けて2件目）
                db.execute('''
                    INSERT INTO application_history
                        (item_id, applicant, application_content, applicant_comment, application_datetime, approver, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    id, applicant, "持ち出し申請", applicant_comment, now_str, approver, "申請中"
                ))

                # 持ち出し申請 child_item 登録（開始・終了日もセット、ON CONFLICTで上書き）
                start_date = request.args.get('start_date', '')
                end_date = request.args.get('end_date', '')
                owners = request.args.getlist(f"owner_list_{id}")
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
                        (id, idx, owner, "持ち出し申請中", start_date, end_date)
                    )

        db.commit()

        # 申請内容を再取得して表示
        items = [dict(row) for row in db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        )]
        return render_template('apply_form.html', items=items, fields=INDEX_FIELDS, message="申請が完了しました（ダイアログで通知：本来はメール送信）", finish=True)

    return redirect(url_for('index'))

@app.route('/approval', methods=['GET', 'POST'])
@login_required
def approval():
    db = get_db()
    username = g.user['username']

    # 自分が承認者で申請中の案件のみ表示
    items = db.execute(
        "SELECT * FROM application_history WHERE approver=? AND status=? ORDER BY application_datetime DESC",
        (username, "申請中")
    ).fetchall()

    if request.method == 'POST':
        selected_ids = request.form.getlist('selected_ids')
        comment = request.form.get('approve_comment', '').strip()
        action = request.form.get('action')
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not selected_ids:
            flash("対象を選択してください")
            # 再取得
            items = db.execute(
                "SELECT * FROM application_history WHERE approver=? AND status=? ORDER BY application_datetime DESC",
                (username, "申請中")
            ).fetchall()
            return render_template('approval.html', items=items, fields=INDEX_FIELDS)

        for application_id in selected_ids:
            app_row = db.execute("SELECT * FROM application_history WHERE id=?", (application_id,)).fetchone()
            if not app_row:
                continue
            item_id = app_row['item_id']
            content = app_row['application_content']

            if action == 'approve':
                # application_historyテーブル更新
                db.execute(
                    '''
                    UPDATE application_history SET
                        approver_comment=?,
                        approval_datetime=?,
                        status=?
                    WHERE id=?
                    ''',
                    (comment, now_str, "承認", application_id)
                )
                # 業務テーブル更新
                if content == "入庫申請":
                    db.execute("UPDATE item SET status=?, sample_manager=? WHERE id=?", ("入庫", username, item_id))
                elif content == "持ち出し申請":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))
                    db.execute("UPDATE child_item SET status=? WHERE item_id=?", ("持ち出し中", item_id))

            elif action == 'reject':
                db.execute(
                    '''
                    UPDATE application_history SET
                        approver_comment=?,
                        approval_datetime=?,
                        status=?
                    WHERE id=?
                    ''',
                    (comment, now_str, "差し戻し", application_id)
                )
                # 差し戻し時、item/child_itemのstatus更新が必要ならここに追加

        db.commit()
        # 完了メッセージと空リストで再描画
        return render_template('approval.html', items=[], fields=INDEX_FIELDS, message="処理が完了しました", finish=True)

    # 初期GET時
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

@app.route('/bulk_manager_change', methods=['GET', 'POST'])
@login_required
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
        return redirect(url_for('index'))
    return render_template('bulk_manager_change.html', items=items, ids=ids_str)

if __name__ == '__main__':
    init_db()
    init_user_db()
    init_child_item_db()
    init_application_history_db()
    app.run(debug=True)
