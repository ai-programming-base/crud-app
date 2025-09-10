# blueprints/raise_request_bp.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, g
import json

from services import (
    get_db,
    login_required, roles_required,
    USER_FIELDS, FIELDS, FIELD_KEYS,
    load_select_fields,  # 既存のローダーを利用（パス問題を回避）
)

raise_request_bp = Blueprint("raise_request_bp", __name__)

@raise_request_bp.route('/raise_request', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def raise_request():
    # 選択式フィールド定義のロード（ファイルが無ければ {} が返る想定）
    select_fields = load_select_fields()

    if request.method == 'POST':
        user_values = {}
        errors = []
        for f in USER_FIELDS:
            v = request.form.get(f['key'], '').strip()
            user_values[f['key']] = v
            if f.get('required') and not v:
                errors.append(f"{f['name']}（必須）を入力してください。")
            # int型バリデーション
            if f.get('type') == 'int' and v:
                if not v.isdigit() or int(v) < 1:
                    errors.append(f"{f['name']}は1以上の整数で入力してください。")

        # 内部項目の初期値
        internal_values = []
        for f in FIELDS:
            if f.get('internal', False):
                if f['key'] == 'status':
                    internal_values.append("入庫前")
                else:
                    internal_values.append("")

        # DB登録用（定義順で揃える）
        values = [user_values.get(f['key'], '') for f in USER_FIELDS] + internal_values

        if errors:
            for msg in errors:
                flash(msg)
            return render_template('raise_request.html',
                                   fields=USER_FIELDS,
                                   values=user_values,
                                   select_fields=select_fields)

        db = get_db()
        db.execute(
            f'INSERT INTO item ({",".join(FIELD_KEYS)}) VALUES ({",".join(["?"]*len(FIELD_KEYS))})',
            values
        )
        db.commit()

        if 'add_and_next' in request.form:
            return render_template('raise_request.html',
                                   fields=USER_FIELDS,
                                   values=user_values,
                                   select_fields=select_fields,
                                   message="登録しました。同じ内容で新規入力できます。")
        else:
            if request.form.get('from_menu') or request.args.get('from_menu'):
                return redirect(url_for('menu'))
            else:
                return redirect(url_for('index_bp.index'))

    # --- GET（コピー起票サポート）---
    copy_id = request.args.get("copy_id")
    values = {f['key']: "" for f in USER_FIELDS}
    if copy_id:
        db = get_db()
        item = db.execute("SELECT * FROM item WHERE id=?", (copy_id,)).fetchone()
        if item:
            item = dict(item)
            for f in USER_FIELDS:
                values[f['key']] = str(item.get(f['key'], '')) if item.get(f['key']) is not None else ""

    return render_template('raise_request.html',
                           fields=USER_FIELDS,
                           values=values,
                           select_fields=select_fields)


@raise_request_bp.route('/delete_selected', methods=['POST'])
@login_required
@roles_required('admin', 'manager')
def delete_selected():
    ids = request.form.getlist('selected_ids')
    if ids:
        db = get_db()
        db.executemany('DELETE FROM item WHERE id=?', [(item_id,) for item_id in ids])
        db.commit()
    if request.form.get('from_menu') or request.args.get('from_menu'):
        return redirect(url_for('menu'))
    else:
        return redirect(url_for('index_bp.index'))
