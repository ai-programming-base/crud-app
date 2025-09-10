# blueprints/select_field_config_bp.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from services import (
    get_db,  # 未使用でも将来拡張用に残してOK
    login_required, roles_required,
    FIELDS, load_select_fields, save_select_fields,
)

select_field_config_bp = Blueprint("select_field_config_bp", __name__)

@select_field_config_bp.route('/select_field_config', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager')
def select_field_config():
    # fields.jsonのユーザー項目のみ
    fields = [f for f in FIELDS if not f.get('internal')]
    # 現在の選択式設定をロード
    select_fields = load_select_fields()

    if request.method == 'POST':
        new_select_fields = {}
        for f in fields:
            key = f['key']
            if request.form.get(f"use_{key}") == "1":
                options = request.form.get(f"options_{key}", "")
                opts = [o.strip() for o in options.split(",") if o.strip()]
                if opts:
                    new_select_fields[key] = opts
        save_select_fields(new_select_fields)
        flash("設定を保存しました")
        return redirect(url_for('select_field_config_bp.select_field_config'))

    return render_template("select_field_config.html", fields=fields, select_fields=select_fields)
