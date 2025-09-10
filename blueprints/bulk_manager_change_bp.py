# blueprints/bulk_manager_change_bp.py
import json
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from services import (
    get_db,
    login_required, roles_required,
    INDEX_FIELDS,
    get_proper_users,
    get_user_profiles,
    get_user_profile,
)

from send_mail import send_mail

bulk_manager_change_bp = Blueprint("bulk_manager_change_bp", __name__)

@bulk_manager_change_bp.route('/bulk_manager_change', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def bulk_manager_change():
    ids_str = request.args.get('ids') if request.method == 'GET' else request.form.get('ids')
    if not ids_str:
        flash("通し番号が指定されていません")
        return redirect(url_for('index_bp.index'))

    id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]
    db = get_db()
    items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
    proper_users = get_proper_users(db)
    proper_usernames = [u['username'] for u in proper_users]
    proper_users_json = [
        {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', ''),
            'email': u.get('email', '')
        } for u in proper_users
    ]

    # 一覧表示用に現在の管理者プロフィールを取得
    current_manager_usernames = list({item['sample_manager'] for item in items})
    display_profiles = get_user_profiles(db, current_manager_usernames)

    if request.method == 'POST':
        new_manager = request.form.get('new_manager', '').strip()
        if not new_manager or new_manager not in proper_usernames:
            # エラー表示時も profiles を渡す
            items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
            current_manager_usernames = list({item['sample_manager'] for item in items})
            display_profiles = get_user_profiles(db, current_manager_usernames)

            return render_template(
                'bulk_manager_change.html',
                items=items, ids=ids_str,
                proper_users=proper_users_json,
                manager_default=g.user['username'],
                profiles=display_profiles,
                error_message="管理者は候補から選択してください。"
            )

        # 旧管理者（変更前）を確定（更新前 items を使用）
        old_managers = set(item['sample_manager'] for item in items)

        # 一括更新
        db.executemany("UPDATE item SET sample_manager=? WHERE id=?", [(new_manager, item['id']) for item in items])
        db.commit()

        # === 通知メール ===
        usernames_to_fetch = list(old_managers | {new_manager})
        profiles = get_user_profiles(db, usernames_to_fetch)

        new_prof = profiles.get(new_manager, {"email": "", "realname": "", "department": ""})
        old_profs = [profiles[u] for u in old_managers if u in profiles]

        # 宛先: 旧管理者 + 新管理者 + 変更者
        to_emails = {p.get("email") for p in old_profs if p.get("email")}
        if new_prof.get("email"):
            to_emails.add(new_prof["email"])

        changer_prof = get_user_profile(db, g.user["username"])
        if changer_prof.get("email"):
            to_emails.add(changer_prof["email"])

        subject = f"[通知] 管理者一括変更 ({len(items)}件)"
        body = render_template(
            "mails/manager_change.txt",
            new_prof=new_prof,
            old_profs=old_profs,
            items=items,
            profiles=profiles,
            changer_prof=changer_prof
        )

        to = ",".join(sorted(e for e in to_emails if e))
        result = send_mail(to=to, subject=subject, body=body)

        if result:
            flash(f"管理者を「{new_manager}」に一括変更しました。旧管理者・新管理者にメールで連絡しました。")
        else:
            flash(f"管理者を「{new_manager}」に一括変更しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        return redirect(url_for('index_bp.index'))

    manager_default = g.user['username']
    return render_template(
        'bulk_manager_change.html',
        items=items, ids=ids_str,
        proper_users=proper_users_json,
        manager_default=manager_default,
        profiles=display_profiles,
        error_message=None
    )
