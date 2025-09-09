# blueprints/child_items_bp.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, g
from services import (
    get_db,
    login_required, roles_required,
    get_user_profiles,
    INDEX_FIELDS,
    logger,
)

child_items_bp = Blueprint("child_items_bp", __name__)

@child_items_bp.route('/child_items')
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def child_items():
    ids_str = request.args.get('ids', '')
    if not ids_str:
        flash("通し番号が指定されていません")
        return redirect(url_for('index_bp.index'))
    try:
        id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]
    except Exception:
        flash("不正なID指定")
        return redirect(url_for('index_bp.index'))
    if not id_list:
        flash("通し番号が指定されていません")
        return redirect(url_for('index_bp.index'))

    db = get_db()

    # 子アイテム
    q = f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(id_list))}) ORDER BY item_id, branch_no"
    child_items = db.execute(q, id_list).fetchall()

    # 親アイテム（概要表示 & 名称マップ）
    items_rows = db.execute(
        f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))}) ORDER BY id",
        id_list
    ).fetchall()
    items = [dict(r) for r in items_rows]
    item_map = {r['id']: dict(r) for r in items_rows}

    # ===== サンプル数を動的算出（破棄・譲渡を除外） =====
    agg_sql = f"""
        SELECT
            item_id,
            COUNT(*) AS child_total,
            SUM(CASE WHEN status NOT IN (?, ?) THEN 1 ELSE 0 END) AS alive_cnt
        FROM child_item
        WHERE item_id IN ({','.join(['?']*len(id_list))})
        GROUP BY item_id
    """
    agg_params = ("破棄", "譲渡", *id_list)
    agg_rows = db.execute(agg_sql, agg_params).fetchall()
    agg_map = {row["item_id"]: {"child_total": row["child_total"], "alive_cnt": row["alive_cnt"]} for row in agg_rows}

    # 各親アイテムに sample_count を付与
    for it in items:
        it_id = it["id"]
        stats = agg_map.get(it_id)
        if not stats or stats["child_total"] == 0:
            it["sample_count"] = it.get("num_of_samples", 0)
        else:
            it["sample_count"] = stats["alive_cnt"] or 0

    # ▼ 持ち出し申請履歴
    hist_rows = db.execute(
        f"""
        SELECT item_id, checkout_start_date, checkout_end_date
        FROM checkout_history
        WHERE item_id IN ({','.join(['?']*len(id_list))})
        ORDER BY item_id, checkout_start_date DESC, checkout_end_date DESC
        """,
        id_list
    ).fetchall()

    checkout_histories = []
    for r in hist_rows:
        checkout_histories.append({
            'item_id': r['item_id'],
            'product_name': item_map.get(r['item_id'], {}).get('product_name', ''),
            'checkout_start_date': r['checkout_start_date'],
            'checkout_end_date': r['checkout_end_date'],
        })

    # ▼ 所有者 & サンプル管理者のプロフィール
    owner_usernames = list({ci['owner'] for ci in child_items if ci['owner']})
    sample_manager_usernames = list({it.get('sample_manager') for it in items if it.get('sample_manager')})
    usernames = list({*owner_usernames, *sample_manager_usernames})
    profiles = get_user_profiles(db, usernames)

    return render_template(
        'child_items.html',
        child_items=child_items,
        item_map=item_map,
        items=items,
        fields=INDEX_FIELDS,
        checkout_histories=checkout_histories,
        profiles=profiles,
    )
