# blueprints/return_request_bp.py
import json
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from services import (
    get_db,
    login_required, roles_required,
    INDEX_FIELDS,
    get_managers_by_department,
    get_user_profiles,
)

from send_mail import send_mail

return_request_bp = Blueprint("return_request_bp", __name__)

@return_request_bp.route('/return_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def return_request():
    db = get_db()

    # realname 優先の表示名マップ（indexと同じ）
    user_rows = db.execute("""
        SELECT username,
            COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    # 申請フォーム表示（POST:選択済みID受取→フォーム表示）
    if request.method == 'POST':
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index_bp.index'))
        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        not_accepted = [str(row['id']) for row in items if row['status'] != "持ち出し中"]
        if not_accepted:
            flash(f"持ち出し中でないアイテム（ID: {', '.join(not_accepted)}）は持ち出し終了申請できません。")
            return redirect(url_for('index_bp.index'))

        item_list = []
        for item in items:
            item = dict(item)
            item_id = item['id']
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?", (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item['sample_count'] = item.get('num_of_samples', 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item['sample_count'] = cnt
            item_list.append(item)

        department = g.user['department']
        all_managers = get_managers_by_department(None, db)
        # 並び順: 同じ部門→それ以外
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''
        return render_template(
            'return_form.html',
            items=item_list, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            user_display=user_display,
        )

    # 申請フォームからの送信時（GET, action=submit）
    if request.args.get('action') == 'submit':
        item_ids = request.args.getlist('item_id')
        checkeds = request.args.getlist('qty_checked')
        if not checkeds or len(checkeds) != len(item_ids):
            flash("全ての数量チェックを確認してください")
            return redirect(url_for('index_bp.index'))

        applicant = g.user['username']
        applicant_comment = request.args.get('comment', '')
        approver = request.args.get('approver', '')
        return_date = request.args.get('return_date', datetime.now().strftime("%Y-%m-%d"))
        storage = request.args.get('storage', '')

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for id in item_ids:
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            original_status = item['status']

            db.execute("UPDATE item SET status=? WHERE id=?", ("返却申請中", id))
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            new_values = dict(item)
            new_values['return_date'] = return_date
            new_values['storage'] = storage
            new_values['status'] = "返却申請中"

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id, json.dumps(new_values, ensure_ascii=False), applicant, applicant_comment, approver, "申請中", now_str, original_status
            ))
        db.commit()

        # ==== メール送信部（承認者・申請者・管理者）====

        # 対象 item の管理者（username）を収集
        items_rows = db.execute(
            f"SELECT id, sample_manager FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        manager_usernames = {row['sample_manager'] for row in items_rows if row and row['sample_manager']}

        # 申請者・承認者・管理者のプロフィールを一括取得
        usernames_to_fetch = {approver, applicant} | manager_usernames
        profiles = get_user_profiles(db, list(usernames_to_fetch))

        approver_prof  = profiles.get(approver,  {})
        applicant_prof = profiles.get(applicant, {})
        manager_profs  = [profiles[u] for u in sorted(manager_usernames)]

        # 宛先（重複除去）
        to_emails = set()
        for p in [approver_prof, applicant_prof] + manager_profs:
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        # 表示用の changes（itemとその管理者）
        changes = [{"item_id": row["id"], "manager": row["sample_manager"]} for row in items_rows]

        # 件名
        subject = f"[申請] 返却申請の保存（{len(changes)}件）"

        # 本文（usernameは本文に出さない）
        body = render_template(
            "mails/return_request.txt",
            approver_prof=approver_prof,
            applicant_prof=applicant_prof,
            manager_profs=manager_profs,
            changes=changes,
            profiles=profiles,
            return_date=return_date,
            storage=storage,
            applicant_comment=applicant_comment,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash("返却申請を保存しました。承認待ちです。承認者ほか関係者にメールで連絡しました。")
        else:
            flash("返却申請を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")
        # ==== ここまで ====

        return redirect(url_for('index_bp.index'))
    
    return redirect(url_for('index_bp.index'))
