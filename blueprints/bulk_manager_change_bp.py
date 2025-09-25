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

# 括管理者変更を受け付ける item.status を一箇所で定義
BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES = ('保管中', '持ち出し中', '返却済')

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

    # 対象アイテム取得
    items_all = db.execute(
        f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})",
        id_list
    ).fetchall()

    # ▼ 追加：許可ステータスで絞り込み（入庫／持ち出し中／返却済）
    allowed_ids = [row['id'] for row in items_all if row['status'] in BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES]
    excluded_ids = [row['id'] for row in items_all if row['status'] not in BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES]
    if excluded_ids:
        flash(f"許可外の状態のアイテムを除外しました（ID: {', '.join(map(str, excluded_ids))}）。"
              f"許可状態: {', '.join(BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES)}")
    if not allowed_ids:
        flash(f"選択されたアイテムは一括管理者変更の対象状態ではありません（許可: {', '.join(BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES)}）。")
        return redirect(url_for('index_bp.index'))

    # 許可対象のみを再取得（以降の処理はこの items を使用）
    items = db.execute(
        f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
        allowed_ids
    ).fetchall()

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
            items_err = db.execute(
                f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
                allowed_ids
            ).fetchall()
            current_manager_usernames = list({item['sample_manager'] for item in items_err})
            display_profiles = get_user_profiles(db, current_manager_usernames)

            return render_template(
                'bulk_manager_change.html',
                items=items_err, ids=','.join(map(str, allowed_ids)),
                proper_users=proper_users_json,
                manager_default=g.user['username'],
                profiles=display_profiles,
                error_message="管理者は候補から選択してください。"
            )

        # ▼ 送信時点でもう一度、最新の status を確認（競合対策）
        rows_status = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
            allowed_ids
        ).fetchall()
        allowed_now_ids = [r['id'] for r in rows_status if r['status'] in BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES]
        not_allowed_now_ids = [r['id'] for r in rows_status if r['status'] not in BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES]
        if not_allowed_now_ids:
            flash(f"送信中に状態が変更されたため、一部のアイテムを除外しました（ID: {', '.join(map(str, not_allowed_now_ids))}）。"
                  f"許可状態: {', '.join(BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES)}")

        if not allowed_now_ids:
            flash("全ての対象が許可外状態となったため、変更は行いませんでした。")
            return redirect(url_for('index_bp.index'))

        # 旧管理者（変更前）を確定（更新対象 items のみ）
        items_before = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_now_ids))})",
            allowed_now_ids
        ).fetchall()
        old_managers = set(item['sample_manager'] for item in items_before)

        # ▼ 念押し：更新直前にも状態確認（トランザクション境界での競合に備える）
        rows_status_final = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(allowed_now_ids))})",
            allowed_now_ids
        ).fetchall()
        allowed_final_ids = [r['id'] for r in rows_status_final if r['status'] in BULK_MANAGER_CHANGE_ALLOWED_ITEM_STATUSES]
        if not allowed_final_ids:
            flash("更新直前に全ての対象が許可外状態となったため、変更は適用されませんでした。")
            return redirect(url_for('index_bp.index'))

        # 一括更新（許可最終確認を通過したIDだけ）
        db.executemany(
            "UPDATE item SET sample_manager=? WHERE id=?",
            [(new_manager, i) for i in allowed_final_ids]
        )
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

        # メール本文に載せるのは実際に更新した items のみ
        items_updated = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_final_ids))})",
            allowed_final_ids
        ).fetchall()

        subject = f"[通知] 管理者一括変更 ({len(items_updated)}件)"
        body = render_template(
            "mails/manager_change.txt",
            new_prof=new_prof,
            old_profs=old_profs,
            items=items_updated,
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
        items=items,                                   # 許可対象のみ
        ids=','.join(map(str, allowed_ids)),           # 許可対象のIDだけを保持
        proper_users=proper_users_json,
        manager_default=manager_default,
        profiles=display_profiles,
        error_message=None
    )
