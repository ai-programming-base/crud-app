# blueprints/entry_request_bp.py
import json
from datetime import datetime
from collections import defaultdict

from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from services import (
    get_db,
    login_required, roles_required,
    INDEX_FIELDS,
    get_managers_by_department, get_proper_users, get_partner_users,
    get_user_profiles,
)
from send_mail import send_mail

entry_request_bp = Blueprint("entry_request_bp", __name__)

@entry_request_bp.route('/entry_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def entry_request():
    db = get_db()

    # properユーザーリスト
    proper_users = get_proper_users(db)  # [{'username':..., 'department':..., 'realname':...}, ...]
    proper_usernames = [u['username'] for u in proper_users]
    proper_users_json = [
        {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        } for u in proper_users
    ]

    # partnerユーザーリスト
    partner_users = get_partner_users(db)
    owner_candidates_map = {}
    for u in (proper_users + partner_users):
        owner_candidates_map[u['username']] = {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        }
    owner_candidates = list(owner_candidates_map.values())

    # POST: 選択ID受取→申請フォーム表示
    if request.method == 'POST' and not request.form.get('action'):
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index_bp.index'))

        # ▼ 入庫前限定チェック
        all_items = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        allowed_ids = [str(r['id']) for r in all_items if r['status'] == '入庫前']

        if not allowed_ids:
            flash("選択されたアイテムは入庫前のものがありません。入庫前のアイテムのみ選択してください。")
            return redirect(url_for('index_bp.index'))

        if len(allowed_ids) != len(item_ids):
            flash("入庫前以外のアイテムは申請対象から除外しました。")

        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})", allowed_ids
        ).fetchall()
        items = [dict(row) for row in items]

        department = g.user['department']
        # manager権限ユーザーリスト取得
        all_managers = get_managers_by_department(None, db)
        # 並べ替え：同部門→他部門
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''

        # 管理者欄のデフォルト
        manager_default = g.user['username']

        return render_template(
            'entry_request.html',
            items=items, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            proper_users=proper_users_json,
            owner_candidates=owner_candidates,
            manager_default=manager_default,
            g=g,
        )

    # 申請フォーム送信（action=submit: 必須項目チェック＆申請内容をitem_applicationに登録、item.statusのみ即更新）
    if (request.method == 'GET' and request.args.get('action') == 'submit') or \
       (request.method == 'POST' and request.form.get('action') == 'submit'):
        form = request.form if request.method == 'POST' else request.args

        item_ids = form.getlist('item_id')
        if not item_ids:
            flash("申請対象が不正です")
            return redirect(url_for('index_bp.index'))

        # 必須入力チェック
        manager = form.get('manager', '').strip()
        comment = form.get('comment', '').strip()
        approver = form.get('approver', '').strip()
        qty_checked = form.getlist('qty_checked')
        with_checkout = form.get("with_checkout") == "1"

        # ▼ 新規：譲渡申請情報の取得
        with_transfer = form.get("with_transfer") == "1" if with_checkout else False
        transfer_branch_ids = form.getlist("transfer_branch_ids") if with_checkout and with_transfer else []
        transfer_comment = form.get("transfer_comment", "") if with_checkout and with_transfer else ""
        transfer_date = form.get("transfer_date", "") if with_checkout and with_transfer else ""

        errors = []
        if not manager or manager not in proper_usernames:
            errors.append("管理者の入力が不正です。正しい管理者を選択してください。")
        if not approver:
            errors.append("承認者を選択してください。")
        if len(qty_checked) != len(item_ids):
            errors.append("すべての数量チェックをしてください。")
        if with_checkout and with_transfer:
            if not transfer_branch_ids:
                errors.append("譲渡する枝番を選択してください。")
            if not transfer_comment.strip():
                errors.append("譲渡コメントを入力してください。")

        # ▼ 状態チェック: 入庫前のみ許可
        rows = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})", item_ids
        ).fetchall()
        not_allowed = [str(r['id']) for r in rows if r['status'] != '入庫前']
        if not_allowed:
            errors.append(f"入庫前ではないアイテムが含まれています（ID: {', '.join(not_allowed)}）。入庫前のみ申請可能です。")

        if errors:
            # 再表示用: 入庫前のみ残して描画
            allowed_ids = [str(r['id']) for r in rows if r['status'] == '入庫前']
            if allowed_ids:
                items = db.execute(
                    f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})", allowed_ids
                ).fetchall()
            else:
                items = []
            items = [dict(row) for row in items]

            department = g.user['department']
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''
            manager_default = g.user['username']
            error_dialog_message = " ".join(errors)
            return render_template(
                'entry_request.html',
                items=items, fields=INDEX_FIELDS,
                approver_default=approver_default,
                approver_list=sorted_managers,
                proper_users=proper_users_json,
                owner_candidates=owner_candidates,
                manager_default=manager_default,
                g=g,
                error_dialog_message=error_dialog_message,
            )

        # ▼ ステータス分岐
        if with_checkout and with_transfer:
            new_status = "入庫持ち出し譲渡申請中"
        elif with_checkout:
            new_status = "入庫持ち出し申請中"
        else:
            new_status = "入庫申請中"

        applicant = g.user['username']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_date = form.get('start_date', '')
        end_date = form.get('end_date', '')

        # 持ち出し同時申請時の所有者欄
        owner_lists = {}
        for id in item_ids:
            owner_lists[str(id)] = form.getlist(f'owner_list_{id}')

        for id in item_ids:
            # item_applicationに申請内容を登録
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()

            # ▼ 念押し: 入庫前以外はスキップ
            if item['status'] != '入庫前':
                continue

            original_status = item['status']

            # itemのstatusのみ即時変更
            db.execute("UPDATE item SET status=? WHERE id=?", (new_status, id))

            # item内容を取得し、申請内容をnew_values(dict)として用意
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            new_values = dict(item)
            new_values['sample_manager'] = manager
            # ▼ 新規：譲渡申請情報
            if with_checkout and with_transfer:
                new_values['status'] = "入庫持ち出し譲渡申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])
                new_values['transfer_date'] = transfer_date
                # transfer_branch_ids 形式は itemID_branchNo。ここではitemごとに格納
                transfer_ids_this = []
                for t in transfer_branch_ids:
                    try:
                        tid, branch_no = t.split("_")
                        if str(tid) == str(id):
                            transfer_ids_this.append(int(branch_no))
                    except Exception:
                        continue
                new_values['transfer_branch_nos'] = transfer_ids_this
                new_values['transfer_comment'] = transfer_comment
            elif with_checkout:
                new_values['status'] = "入庫持ち出し申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])
            else:
                new_values['status'] = "入庫申請中"

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id, json.dumps(new_values, ensure_ascii=False), applicant, comment, approver, "申請中", now_str, original_status
            ))

        db.commit()

        # ==== メール送信部（承認者・申請者・管理者・※with_checkout時は所有者も）====
        if with_checkout and with_transfer:
            subject_kind = "入庫持ち出し譲渡申請"
        elif with_checkout:
            subject_kind = "入庫持ち出し申請"
        else:
            subject_kind = "入庫申請"

        # item の管理者はフォーム指定の単一ユーザー
        manager_username = manager

        # itemごとの owners（username配列）
        owners_by_item = {}
        if with_checkout:
            for id in item_ids:
                owners_by_item[int(id)] = [u for u in owner_lists.get(str(id), []) if u]

        # itemごとの譲渡枝番（整数リスト）
        transfer_branches_by_item = defaultdict(list)
        if with_checkout and with_transfer and transfer_branch_ids:
            for t in transfer_branch_ids:  # "itemID_branchNo" 形式
                try:
                    tid, branch_no = t.split("_")
                    transfer_branches_by_item[int(tid)].append(int(branch_no))
                except Exception:
                    continue
            for k in list(transfer_branches_by_item.keys()):
                transfer_branches_by_item[k] = sorted(transfer_branches_by_item[k])

        # changes 構造
        changes = []
        for id in item_ids:
            iid = int(id)
            changes.append({
                "item_id": iid,
                "manager": manager_username,
                "owners": owners_by_item.get(iid, []),
                "transfer_branch_nos": transfer_branches_by_item.get(iid, []),
            })

        # プロフィールを一括取得（宛先・本文用）
        usernames_to_fetch = {approver, applicant, manager_username}
        if with_checkout:
            all_owner_usernames = {u for arr in owners_by_item.values() for u in arr}
            usernames_to_fetch |= all_owner_usernames

        profiles = get_user_profiles(db, list(usernames_to_fetch))
        approver_prof  = profiles.get(approver,  {})
        applicant_prof = profiles.get(applicant, {})
        manager_prof   = profiles.get(manager_username, {})
        owner_profs    = []
        if with_checkout:
            owner_profs = [profiles[u] for u in sorted({u for arr in owners_by_item.values() for u in arr})]

        # 宛先（重複除去）
        to_emails = set()
        for p in [approver_prof, applicant_prof, manager_prof] + (owner_profs if with_checkout else []):
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        subject = f"[申請] {subject_kind}の保存（{len(changes)}件）"

        # 本文（usernameは本文に出さない）
        body = render_template(
            "mails/entry_request.txt",
            approver_prof=approver_prof,
            applicant_prof=applicant_prof,
            manager_prof=manager_prof,
            owner_profs=owner_profs,
            changes=changes,
            profiles=profiles,
            with_checkout=with_checkout,
            with_transfer=with_transfer,
            start_date=start_date,
            end_date=end_date,
            comment=comment,
            transfer_comment=transfer_comment,
            transfer_date=transfer_date,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash(f"{subject_kind}を保存しました。承認待ちです。承認者ほか関係者にメールで連絡しました。")
        else:
            flash(f"{subject_kind}を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        return redirect(url_for('index_bp.index'))

    return redirect(url_for('index_bp.index'))
