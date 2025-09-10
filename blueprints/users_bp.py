# blueprints/users_bp.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash
from services import get_db, login_required, roles_required

users_bp = Blueprint("users_bp", __name__)

@users_bp.route('/register', methods=['GET', 'POST'])
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

        if not username or not password or not email:
            error = 'ユーザー名、パスワード、メールは必須です'
        elif db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            error = 'そのユーザー名は既に使われています'
        else:
            db.execute(
                "INSERT INTO users (username, password, email, department, realname) VALUES (?, ?, ?, ?, ?)",
                (username, generate_password_hash(password), email, department, realname)
            )
            user_id = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()['id']
            for role_id in selected_roles:
                db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
            db.commit()
            flash('ユーザー登録が完了しました')
            return redirect(url_for('users_bp.users_list'))

    return render_template('register.html', roles=roles)

@users_bp.route('/users', methods=['GET'])
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

@users_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager')
def edit_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("対象ユーザーが見つかりません")
        return redirect(url_for('index_bp.index'))

    roles = db.execute("SELECT id, name FROM roles").fetchall()
    cur_role_rows = db.execute("SELECT role_id FROM user_roles WHERE user_id=?", (user_id,)).fetchall()
    current_role_ids = {str(r['role_id']) for r in cur_role_rows}

    is_admin = ('admin' in g.user_roles)
    is_manager = ('manager' in g.user_roles)

    if request.method == 'POST':
        selected_roles = request.form.getlist('roles')

        if is_admin:
            password = request.form.get('password', '')
            email = (request.form.get('email') or '').strip()
            department = (request.form.get('department') or '').strip()
            realname = (request.form.get('realname') or '').strip()

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

                db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
                for role_id in selected_roles:
                    db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))

                db.commit()
                flash('ユーザー情報を更新しました')
                return redirect(url_for('users_bp.edit_user', user_id=user_id))

        elif is_manager:
            db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
            for role_id in selected_roles:
                db.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
            db.commit()
            flash('ロールを更新しました')
            return redirect(url_for('users_bp.edit_user', user_id=user_id))

    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    cur_role_rows = db.execute("SELECT role_id FROM user_roles WHERE user_id=?", (user_id,)).fetchall()
    current_role_ids = {str(r['role_id']) for r in cur_role_rows}

    return render_template('user_edit.html',
                           user=user,
                           roles=roles,
                           current_role_ids=current_role_ids)
