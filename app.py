from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3

app = Flask(__name__)
app.secret_key = "any_secret"  # フラッシュメッセージ用
DATABASE = 'items.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                field1 TEXT, field2 TEXT, field3 TEXT, field4 TEXT, field5 TEXT,
                field6 TEXT, field7 TEXT, field8 TEXT, field9 TEXT, field10 TEXT
            )
        ''')

@app.route('/')
def index():
    db = get_db()
    items = db.execute('SELECT * FROM item').fetchall()
    return render_template('index.html', items=items)

@app.route('/add', methods=['GET', 'POST'])
def add():
    if request.method == 'POST':
        values = [request.form.get(f'field{i}', '').strip() for i in range(1, 11)]
        errors = []
        if not values[0]:
            errors.append("field1（必須）を入力してください。")
        if not values[1]:
            errors.append("field2（必須）を入力してください。")
        if errors:
            for msg in errors:
                flash(msg)
            return render_template('form.html', values=values)
        db = get_db()
        db.execute(
            'INSERT INTO item (field1,field2,field3,field4,field5,field6,field7,field8,field9,field10) VALUES (?,?,?,?,?,?,?,?,?,?)',
            values
        )
        db.commit()
        return redirect(url_for('index'))
    return render_template('form.html')

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
    ids = request.form.getlist('item_id')
    # 各itemごとに10カラムぶん取ってくる
    all_fields = []
    for i in range(len(ids)):
        fields = []
        for f in range(1, 11):
            # 複数行のfield1,field2...がPOSTされるのでリストになる
            field_list = request.form.getlist(f'field{f}')
            fields.append(field_list[i] if i < len(field_list) else "")
        all_fields.append(fields)
    for i, item_id in enumerate(ids):
        db.execute(
            'UPDATE item SET ' +
            ', '.join([f'field{f}=?' for f in range(1, 11)]) +
            ' WHERE id=?',
            all_fields[i] + [item_id]
        )
    db.commit()
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
