from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import math
import json
import os

app = Flask(__name__)
app.secret_key = "any_secret"
DATABASE = 'items.db'

def load_fields():
    with open('fields.json', encoding='utf-8') as f:
        return json.load(f)

FIELDS = load_fields()
USER_FIELDS = [f for f in FIELDS if not f.get('internal', False)]
INDEX_FIELDS = [f for f in FIELDS if f.get('show_in_index', False)]
FIELD_KEYS = [f['key'] for f in FIELDS]

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS item ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            + ", ".join([f"{f['key']} TEXT" for f in FIELDS])
            + ")"
        )

@app.route('/')
def index():
    page = int(request.args.get('page', 1))
    per_page = 10
    offset = (page - 1) * per_page

    filters = {}
    where = []
    params = []
    # フィルタは表示カラム・編集カラムどちらでも自由
    for f in USER_FIELDS:
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
def add():
    if request.method == 'POST':
        user_values = [request.form.get(f['key'], '').strip() for f in USER_FIELDS]
        errors = []
        for i, f in enumerate(USER_FIELDS):
            if f.get('required') and not user_values[i]:
                errors.append(f"{f['name']}（必須）を入力してください。")
        internal_values = ["" for f in FIELDS if f.get('internal', False)]
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
        return redirect(url_for('index'))
    return render_template('form.html', fields=USER_FIELDS, values=["" for _ in USER_FIELDS])

@app.route('/delete_selected', methods=['POST'])
def delete_selected():
    ids = request.form.getlist('selected_ids')
    if ids:
        db = get_db()
        db.executemany('DELETE FROM item WHERE id=?', [(item_id,) for item_id in ids])
        db.commit()
    return redirect(url_for('index'))

@app.route('/update_items', methods=['POST'])
def update_items():
    db = get_db()
    item_ids = request.form.getlist('item_id')
    errors = []
    for item_id in item_ids:
        row_values = []
        for f in FIELDS:
            if not f.get('internal', False):
                v = request.form.get(f"{f['key']}_{item_id}", '').strip()
                row_values.append(v)
            else:
                current = db.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
                row_values.append(current[f['key']])
        for i, f in enumerate(USER_FIELDS):
            if f.get('required') and not row_values[INDEX_FIELDS.index(f)]:
                errors.append(f"ID {item_id} の {f['name']}（必須）を入力してください。")
        if not errors:
            db.execute(
                f'UPDATE item SET '
                + ', '.join([f"{f['key']}=?" for f in FIELDS])
                + ' WHERE id=?',
                row_values + [item_id]
            )
    if errors:
        for msg in errors:
            flash(msg)
        return redirect(url_for('index'))
    db.commit()
    return redirect(url_for('index'))

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True)
