# blueprints/inventory_bp.py
from datetime import datetime
import calendar
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

    # ===== ページング =====
    per_page_raw = request.args.get('per_page', '20')
    if per_page_raw == 'all':
        per_page = None
        page = 1
        offset = 0
    else:
        per_page = int(per_page_raw)
        page = int(request.args.get('page', 1))
        offset = (page - 1) * per_page

    # ===== 表示名マップ & realname -> username 逆引き =====
    user_rows = db.execute("""
        SELECT username,
               COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}
    display_to_username = {}
    for u, d in user_display.items():
        display_to_username.setdefault(d, u)

    # ===== フィルタ構築 =====
    filters = {}
    where = []
    params = []

    id_filter = request.args.get("id_filter", "").strip()
    filters["id"] = id_filter
    if id_filter:
        where.append("CAST(i.id AS TEXT) LIKE ?")
        params.append(f"%{id_filter}%")

    for f in INDEX_FIELDS:
        key = f["key"]
        v = request.args.get(f"{key}_filter", "").strip()
        if key == "sample_manager" and v:
            normalized = display_to_username.get(v, v)  # realname -> username
            filters[key] = v  # 表示はrealnameのまま
            where.append(f"i.{key} LIKE ?")
            params.append(f"%{normalized}%")
        else:
            filters[key] = v
            if v:
                where.append(f"i.{key} LIKE ?")
                params.append(f"%{v}%")

    # proper は自分の分のみ
    if 'proper' in user_roles and not ('admin' in user_roles or 'manager' in user_roles):
        where.append("i.sample_manager = ?")
        params.append(username)

    # --- 最終棚卸し日（年月）フィルタ：未実施も“以前”として含める ---
    last_checked_ym = request.args.get("last_checked_ym_filter", "").strip()
    filters["last_checked_ym"] = last_checked_ym

    ic_date_clause = ""
    month_end_str = None
    if last_checked_ym:
        # 期待フォーマット: YYYY-MM
        try:
            dt = datetime.strptime(last_checked_ym, "%Y-%m")
            last_day = calendar.monthrange(dt.year, dt.month)[1]
            month_end_str = f"{dt.year:04d}-{dt.month:02d}-{last_day:02d} 23:59:59"
            # ★ 未実施（NULL）も含める： (ic.checked_at IS NULL OR ic.checked_at <= ?)
            ic_date_clause = " AND (ic.checked_at IS NULL OR ic.checked_at <= ?)"
        except ValueError:
            # フォーマット不正なら無視（フィルタ未適用）
            pass

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    # ===== データ取得（最新棚卸し情報をJOIN）=====
    base_sql = f"""
        SELECT i.*,
               ic.checked_at AS last_checked_at,
               ic.checker    AS last_checker
        FROM item i
        LEFT JOIN (
            SELECT a.item_id, a.checked_at, a.checker
            FROM inventory_check a
            INNER JOIN (
                SELECT item_id, MAX(checked_at) AS max_checked
                FROM inventory_check GROUP BY item_id
            ) b
            ON a.item_id = b.item_id AND a.checked_at = b.max_checked
        ) ic ON i.id = ic.item_id
        {where_clause}
    """
    # JOIN 後の ic 条件を足す（NULL も対象にするため LEFT JOIN を維持）
    if ic_date_clause:
        if where_clause:
            base_sql += ic_date_clause
        else:
            base_sql += " WHERE 1=1" + ic_date_clause
    base_sql += " ORDER BY i.id DESC"

    final_params = list(params)
    if ic_date_clause and month_end_str:
        final_params.append(month_end_str)

    rows_all = db.execute(base_sql, final_params).fetchall()

    total = len(rows_all)
    if per_page is not None:
        items = rows_all[offset:offset + per_page]
    else:
        items = rows_all

    # ===== フィルタ候補辞書 =====
    filter_choices_dict = {}
    for f in INDEX_FIELDS:
        col = f["key"]
        r = db.execute(f"""
            SELECT DISTINCT {col}
            FROM item
            WHERE {col} IS NOT NULL AND {col} != ''
        """).fetchall()
        if col == "sample_manager":
            usernames = {str(row[col]) for row in r if row[col] not in (None, '')}
            display_names = {user_display.get(u, u) for u in usernames}
            filter_choices_dict[col] = sorted(display_names)
        else:
            filter_choices_dict[col] = sorted({str(row[col]) for row in r if row[col] not in (None, '')})

    id_rows = db.execute("SELECT id FROM item ORDER BY id DESC").fetchall()
    filter_choices_dict["id"] = [str(r["id"]) for r in id_rows]

    # ===== ページ数 =====
    page_count = 1 if per_page is None else max(1, (total + per_page - 1) // per_page)

    return render_template(
        'inventory_list.html',
        items=items,
        fields=INDEX_FIELDS,
        user_display=user_display,
        # フィルタ/ページング用
        filters=filters,
        per_page=per_page_raw,
        page=page,
        page_count=page_count,
        total=total,
        filter_choices_dict=filter_choices_dict,
    )

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
