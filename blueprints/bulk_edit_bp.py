# blueprints/bulk_edit_bp.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from services import (
    get_db,
    login_required,
    FIELDS,
    acquire_locks, release_locks,
)

bulk_edit_bp = Blueprint("bulk_edit_bp", __name__)

# 一括編集を許可する item.status を一箇所で定義
BULK_EDIT_ALLOWED_ITEM_STATUSES = ('入庫前', '保管中', '持ち出し中', '返却済')

@bulk_edit_bp.route('/bulk_edit')
@login_required
def bulk_edit():
    """
    index から選択したID群を ?ids=1,2,3 で受けてロック→編集画面へ。
    画面表示で sample_manager を index と同じ user_display マップで表示するため、
    users テーブルから display_name を構築してテンプレへ渡す。
    """
    ids_raw = request.args.get('ids', '')
    ids = [s for s in ids_raw.split(',') if s.strip()]
    if not ids:
        flash("編集対象の通し番号を選択してください。")
        return redirect(url_for('index_bp.index'))

    db = get_db()

    # 許可ステータスでフィルタ（入庫前／入庫／持ち出し中／返却済）
    placeholders = ",".join(["?"] * len(ids))
    rows_status = db.execute(
        f"SELECT id, status FROM item WHERE id IN ({placeholders})",
        ids
    ).fetchall()
    allowed_ids = [str(r['id']) for r in rows_status if r['status'] in BULK_EDIT_ALLOWED_ITEM_STATUSES]
    excluded_ids = [str(r['id']) for r in rows_status if r['status'] not in BULK_EDIT_ALLOWED_ITEM_STATUSES]

    if excluded_ids:
        flash(f"許可外の状態のアイテムを除外しました（ID: {', '.join(excluded_ids)}）。"
              f"許可状態: {', '.join(BULK_EDIT_ALLOWED_ITEM_STATUSES)}")
    if not allowed_ids:
        flash(f"選択されたアイテムは一括編集の対象状態ではありません（許可: {', '.join(BULK_EDIT_ALLOWED_ITEM_STATUSES)}）。")
        return redirect(url_for('index_bp.index'))

    # 他ユーザーによる編集中チェック＆ロック取得（▼ 許可IDのみ）
    ok, blocked = acquire_locks(db, allowed_ids, g.user['username'])
    if not ok:
        flash("以下のIDは他ユーザーが編集中のため編集できません: " + ", ".join(blocked))
        return redirect(url_for('index_bp.index'))

    # user_display を作成（realname 優先、なければ username）
    user_rows = db.execute("""
        SELECT
            username,
            COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    # 編集対象の取得（index と同じ項目セット）
    user_field_keys = [f['key'] for f in FIELDS if not f.get('internal', False) and f.get('show_in_index', True)]
    placeholders_allowed = ",".join(["?"] * len(allowed_ids))
    rows = db.execute(
        f"SELECT * FROM item WHERE id IN ({placeholders_allowed}) ORDER BY id DESC",
        allowed_ids
    ).fetchall()
    items = [dict(r) for r in rows]

    return render_template(
        'edit.html',
        items=items,
        fields=[f for f in FIELDS if f.get('show_in_index', True)],
        user_field_keys=user_field_keys,
        user_display=user_display,
    )

@bulk_edit_bp.route('/bulk_edit/commit', methods=['POST'])
@login_required
def bulk_edit_commit():
    db = get_db()
    ids = request.form.getlist('item_id')

    # commit 時も最新 status を再チェックし、許可IDのみ更新
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        rows_status = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({placeholders})",
            ids
        ).fetchall()
        allowed_now_ids = [str(r['id']) for r in rows_status if r['status'] in BULK_EDIT_ALLOWED_ITEM_STATUSES]
        not_allowed_now_ids = [str(r['id']) for r in rows_status if r['status'] not in BULK_EDIT_ALLOWED_ITEM_STATUSES]
        if not_allowed_now_ids:
            flash(f"送信中に状態が変更されたため、一部アイテムの編集をスキップしました（ID: {', '.join(not_allowed_now_ids)}）。"
                  f"許可状態: {', '.join(BULK_EDIT_ALLOWED_ITEM_STATUSES)}")
    else:
        allowed_now_ids = []

    user_field_keys = [f['key'] for f in FIELDS if not f.get('internal', False) and f.get('show_in_index', True)]

    # ▼ 念押し：許可対象のみ更新
    for item_id in allowed_now_ids:
        values = [request.form.get(f"{key}_{item_id}", "") for key in user_field_keys]
        set_clause = ", ".join([f"{key}=?" for key in user_field_keys])
        db.execute(f"UPDATE item SET {set_clause} WHERE id=?", values + [item_id])

    db.commit()
    # ロックのみ解除（statusは触らない）—送信された全IDを解除してロック取り残しを防止
    release_locks(db, [int(x) for x in ids if x.isdigit()])
    flash(f"{len(allowed_now_ids)}件の編集を反映しました。")
    return redirect(url_for('index_bp.index'))

@bulk_edit_bp.route('/bulk_edit/cancel', methods=['POST'])
@login_required
def bulk_edit_cancel():
    db = get_db()
    ids = request.form.getlist('item_id')

    # 値は保存せず、ロックのみ解除（statusは触らない）
    release_locks(db, [int(x) for x in ids if x.isdigit()])
    flash("編集をキャンセルし、ロックを解除しました。")
    return redirect(url_for('index_bp.index'))

@bulk_edit_bp.route('/unlock_my_locks', methods=['POST'])
@login_required
def unlock_my_locks():
    db = get_db()
    # 自分がロックしている全IDを収集して解除
    rows = db.execute("SELECT id FROM item WHERE locked_by=?", (g.user['username'],)).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        release_locks(db, ids)
        flash(f"{len(ids)}件のロックを解除しました。")
    else:
        flash("解除対象のロックはありません。")
    return redirect(url_for('index_bp.index'))
