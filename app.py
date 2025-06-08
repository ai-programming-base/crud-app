from flask import Flask, render_template, request, redirect, url_for
import sqlite3

app = Flask(__name__)
DATABASE = 'items.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS item (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT)')

@app.route('/')
def index():
    db = get_db()
    items = db.execute('SELECT * FROM item').fetchall()
    return render_template('index.html', items=items)

@app.route('/add', methods=['GET', 'POST'])
def add():
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        db = get_db()
        db.execute('INSERT INTO item (name, description) VALUES (?, ?)', (name, description))
        db.commit()
        return redirect(url_for('index'))
    return render_template('form.html', item=None)

@app.route('/item/<int:item_id>', methods=['GET', 'POST'])
def detail(item_id):
    db = get_db()
    item = db.execute('SELECT * FROM item WHERE id=?', (item_id,)).fetchone()
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        db.execute('UPDATE item SET name=?, description=? WHERE id=?', (name, description, item_id))
        db.commit()
        return redirect(url_for('index'))
    return render_template('detail.html', item=item)

@app.route('/delete_selected', methods=['POST'])
def delete_selected():
    ids = request.form.getlist('selected_ids')
    if ids:
        db = get_db()
        db.executemany('DELETE FROM item WHERE id=?', [(item_id,) for item_id in ids])
        db.commit()
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
