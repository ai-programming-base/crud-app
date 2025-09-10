# blueprints/change_owner_bp.py
import json
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from services import (
    get_db,
    login_required, roles_required,
    get_proper_users, get_partner_users,
    get_user_profiles, get_user_profile,
)
from send_mail import send_mail

change_owner_bp = Blueprint("change_owner_bp", __name__)

@change_owner_bp.route('/change_owner', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def change_owner():
    db = get_db()

    # 候補ユーザー（proper + partner）
    proper_users = get_proper_users(db)
    partner_users = get_partner_users(db)
    tmp = {}
    for u in (proper_users + partner_users):
        tmp[u['username']] = {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        }
    owner_candidates = list(tmp.values())

    ids_str = request.args.get('ids') if request.method == 'GET' else request.form.get('ids')
    if not ids_str:
        flash("通し番号が指定されていません")
        return redirect(url_for('index_bp.index'))
    id_list = [int(i) for i in ids_str.split(',') if i.isdigit()]

    # 対象item（持ち出し中のみ）
    items = db.execute(f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list).fetchall()
    target_ids = [item['id'] for item in items if item['status'] == "持ち出し中"]
    if not target_ids:
        flash("選択した中に所有者変更できるアイテムがありません（持ち出し中のみ可能）")
        return redirect(url_for('index_bp.index'))

    # 子アイテム取得（枝番順）
    child_items = db.execute(
        f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(target_ids))}) ORDER BY item_id, branch_no",
        target_ids
    ).fetchall()

    # ★ GET: 一覧表示用に「現在の所有者」のプロフィールを用意
    if request.method == 'GET':
        current_owner_usernames = [ci['owner'] for ci in child_items if ci['owner']]
        owner_profiles = get_user_profiles(db, list(set(current_owner_usernames)))  # dict[username] -> {department, realname, ...}

        return render_template(
            'change_owner.html',
            items=items,
            child_items=child_items,
            ids=','.join(str(i) for i in target_ids),
            owner_candidates=owner_candidates,
            profiles=owner_profiles,
        )

    # POST: 変更反映
    updates = []
    for ci in child_items:
        owner_key = f"owner_{ci['item_id']}_{ci['branch_no']}"
        new_owner = request.form.get(owner_key, '').strip()
        if new_owner and new_owner != ci['owner']:
            updates.append((new_owner, ci['id']))
    if updates:
        db.executemany("UPDATE child_item SET owner=? WHERE id=?", updates)
        db.commit()
        # 履歴記録
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for ci, (new_owner, _) in zip(child_items, updates):
            db.execute('''
                INSERT INTO application_history
                (item_id, applicant, application_content, applicant_comment, application_datetime, approver, status, approval_datetime, approver_comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ci['item_id'], g.user['username'],
                "所有者変更", f"{ci['branch_no']}番 所有者: {ci['owner']}→{new_owner}",
                now_str, "", "承認不要", now_str, ""
            ))
        db.commit()

        # メール通知
        updates_map = {ci_id: new_owner for (new_owner, ci_id) in updates}

        items_by_id = {it['id']: it for it in items}  # item_id -> sqlite3.Row

        changes = []
        manager_usernames = set()
        old_owner_usernames = set()
        new_owner_usernames = set()

        def row_get(row, key, default=""):
            """sqlite3.Row 安全アクセス"""
            if not row:
                return default
            try:
                val = row[key]
                return default if val is None else val
            except Exception:
                return default

        for ci in child_items:
            if ci['id'] not in updates_map:
                continue  # 更新なし
            new_owner = updates_map[ci['id']]
            old_owner = ci['owner']
            if new_owner == old_owner:
                continue

            item = items_by_id.get(ci['item_id'])
            manager = row_get(item, 'sample_manager', '')

            changes.append({
                "item_id": ci['item_id'],
                "branch_no": ci['branch_no'],
                "manager": manager,
                "old_owner": old_owner,
                "new_owner": new_owner,
            })

            if manager:
                manager_usernames.add(manager)
            if old_owner:
                old_owner_usernames.add(old_owner)
            if new_owner:
                new_owner_usernames.add(new_owner)

        # 変更者
        changer_username = g.user['username']

        # プロフィール取得
        all_usernames = ({changer_username} | manager_usernames |
                         old_owner_usernames | new_owner_usernames)
        profiles = get_user_profiles(db, list(all_usernames))

        changer_prof = profiles.get(changer_username, {})
        manager_profs = [profiles[u] for u in sorted(manager_usernames)]
        old_owner_profs = [profiles[u] for u in sorted(old_owner_usernames)]
        new_owner_profs = [profiles[u] for u in sorted(new_owner_usernames)]

        # 宛先（重複除去）
        to_emails = set()
        for p in manager_profs + old_owner_profs + new_owner_profs + [changer_prof]:
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        subject = f"[通知] 所有者変更 ({len(changes)}件)"
        body = render_template(
            "mails/owner_change.txt",
            changer_prof=changer_prof,
            manager_profs=manager_profs,
            old_owner_profs=old_owner_profs,
            new_owner_profs=new_owner_profs,
            changes=changes,
            profiles=profiles,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash("所有者を変更しました。所有者・管理者・変更者にメールで連絡しました。")
        else:
            flash("所有者を変更しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

    else:
        flash("変更はありませんでした。")
    return redirect(url_for('index_bp.index'))
