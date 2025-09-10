# blueprints/inventory_bp.py
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from services import (
    get_db,
    login_required, roles_required,
    INDEX_FIELDS,
)

inventory_bp = Blueprint("inventory_bp", __name__)

@inventory_bp.route('/inventory_list')
@login_required
@roles_required('admin', 'manager', 'proper')
def inventory_list():
    db = get_db()
    username = g.user['username']
    user_roles = g.user_roles

    if 'admin' in user_roles or 'manager' in user_roles:
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
        items = []

    # realname 優先の表示名マップ
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

@inventory_bp.route('/inventory_check', methods=['POST'])
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
    return redirect(url_for('inventory_bp.inventory_list'))

@inventory_bp.route('/inventory_history/<int:item_id>')
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
