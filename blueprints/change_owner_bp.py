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

# 所有者変更を許可する item.status を一箇所で定義
CHANGE_OWNER_ALLOWED_ITEM_STATUSES = ('持ち出し中',)

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

    # 対象item（持ち出し中のみ） → 許可ステータスでフィルタ
    items = db.execute(
        f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(id_list))})", id_list
    ).fetchall()

    # 許可状態で絞り込み（初期は「持ち出し中」のみ）
    target_ids = [item['id'] for item in items if item['status'] in CHANGE_OWNER_ALLOWED_ITEM_STATUSES]
    excluded_ids = [item['id'] for item in items if item['status'] not in CHANGE_OWNER_ALLOWED_ITEM_STATUSES]
    if excluded_ids:
        # 一部だけ許可：除外したIDを通知（従来のメッセージを包括する表現）
        flash(f"所有者変更の対象外状態のアイテムを除外しました（ID: {', '.join(map(str, excluded_ids))}）。"
              f"許可状態: {', '.join(CHANGE_OWNER_ALLOWED_ITEM_STATUSES)}")

    if not target_ids:
        # 従来の文言を踏襲しつつ、許可状態の案内も加える
        flash(f"選択した中に所有者変更できるアイテムがありません（許可状態: {', '.join(CHANGE_OWNER_ALLOWED_ITEM_STATUSES)}）")
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
            items=[it for it in items if it['id'] in target_ids],  # 許可IDのみ渡す
            child_items=child_items,
            ids=','.join(str(i) for i in target_ids),
            owner_candidates=owner_candidates,
            profiles=owner_profiles,
        )

    # POST: 変更反映
    # 送信時も最新状態で許可ステータスを再確認（競合対策）
    rows_status = db.execute(
        f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(target_ids))})",
        target_ids
    ).fetchall()
    allowed_now_ids = {r['id'] for r in rows_status if r['status'] in CHANGE_OWNER_ALLOWED_ITEM_STATUSES}
    not_allowed_now_ids = {r['id'] for r in rows_status if r['status'] not in CHANGE_OWNER_ALLOWED_ITEM_STATUSES}
    if not_allowed_now_ids:
        flash(f"送信中に状態が変更されたため、一部アイテムを除外しました（ID: {', '.join(map(str, sorted(not_allowed_now_ids)))}）。"
              f"許可状態: {', '.join(CHANGE_OWNER_ALLOWED_ITEM_STATUSES)}")

    # フォームからの新所有者を集計（許可IDに絞る）
    updates = []
    for ci in child_items:
        if ci['item_id'] not in allowed_now_ids:
            continue
        owner_key = f"owner_{ci['item_id']}_{ci['branch_no']}"
        new_owner = request.form.get(owner_key, '').strip()
        if new_owner and new_owner != ci['owner']:
            updates.append((new_owner, ci['id']))

    if updates:
        # ▼ 念押し：更新直前に親アイテムの現状態を再確認し、許可外になっていた子はスキップ
        #   （上の allowed_now_ids で基本弾いているが、念のため再取得して防御）
        parent_ids_for_updates = list({ci['item_id'] for ci in child_items if any(u[1] == ci['id'] for u in updates)})
        rows_status2 = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(parent_ids_for_updates))})",
            parent_ids_for_updates
        ).fetchall()
        allowed_final_ids = {r['id'] for r in rows_status2 if r['status'] in CHANGE_OWNER_ALLOWED_ITEM_STATUSES}

        # child_item.id -> parent item_id マップ
        ci_parent_map = {ci['id']: ci['item_id'] for ci in child_items}
        updates_final = [(new_owner, ci_id) for (new_owner, ci_id) in updates if ci_parent_map.get(ci_id) in allowed_final_ids]

        if not updates_final:
            flash("所有者変更対象が許可状態ではなくなったため、変更は適用されませんでした。")
            return redirect(url_for('index_bp.index'))

        db.executemany("UPDATE child_item SET owner=? WHERE id=?", updates_final)
        db.commit()

        # 履歴記録
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # child_item.id -> (old_owner, item_id, branch_no)
        ci_map = {ci['id']: (ci['owner'], ci['item_id'], ci['branch_no']) for ci in child_items}
        for (new_owner, ci_id) in updates_final:
            old_owner, item_id, branch_no = ci_map.get(ci_id, ("", None, None))
            if item_id is None:
                continue
            db.execute('''
                INSERT INTO application_history
                (item_id, applicant, application_content, applicant_comment, application_datetime, approver, status, approval_datetime, approver_comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item_id, g.user['username'],
                "所有者変更", f"{branch_no}番 所有者: {old_owner}→{new_owner}",
                now_str, "", "承認不要", now_str, ""
            ))
        db.commit()

        # メール通知
        updates_map = {ci_id: new_owner for (new_owner, ci_id) in updates_final}

        # 許可IDのみで items_by_id を作る（メールの manager 参照用）
        items_allowed = [it for it in items if it['id'] in allowed_final_ids]
        items_by_id = {it['id']: it for it in items_allowed}  # item_id -> sqlite3.Row

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
            # 許可最終確認：親が許可外なら通知対象からも除外
            if ci['item_id'] not in allowed_final_ids:
                continue

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
