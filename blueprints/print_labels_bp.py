# blueprints/print_labels_bp.py
import os, json
from flask import Blueprint, render_template, request, redirect, url_for, flash
from services import get_db, login_required

print_labels_bp = Blueprint("print_labels_bp", __name__)

@print_labels_bp.route('/print_labels', methods=['GET', 'POST'])
@login_required
def print_labels():
    # 選択されたIDの取得（POST/GET両対応）
    if request.method == 'POST':
        ids = request.form.getlist('selected_ids')
    else:
        ids = request.args.get('ids', '').split(',')

    ids = [int(i) for i in ids if i.isdigit()]
    if not ids:
        flash("ラベル印刷するアイテムを選択してください。")
        return redirect(url_for('index_bp.index'))

    db = get_db()
    items = db.execute(
        f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(ids))})",
        ids
    ).fetchall()
    items = [dict(row) for row in items]
    items_sorted = sorted(items, key=lambda x: x['id'])

    # fields.json はプロジェクト直下想定： .../blueprints/ から一つ上へ
    base_dir = os.path.dirname(os.path.dirname(__file__))
    fields_path = os.path.join(base_dir, 'fields.json')
    with open(fields_path, encoding='utf-8') as f:
        fields = json.load(f)

    return render_template('print_labels.html', items=items_sorted, fields=fields)
