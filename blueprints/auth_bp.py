# blueprints/auth_bp.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from services import get_db
from auth import authenticate

auth_bp = Blueprint("auth_bp", __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        auth_result = authenticate(username, password)
        if auth_result.get('result'):
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if user:
                db.execute("""
                    UPDATE users SET email=?, department=?, realname=? WHERE username=?
                """, (auth_result['email'], auth_result['department'], auth_result['realname'], username))
                db.commit()
                user_id = user['id']
            else:
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

@auth_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('auth_bp.login'))
