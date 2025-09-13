# blueprints/checkout_bp.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, g
from datetime import datetime
from collections import defaultdict
import json

from services import (
    get_db, INDEX_FIELDS, login_required, roles_required,
    get_proper_users, get_partner_users, get_managers_by_department, get_user_profiles,
    logger
)
from send_mail import send_mail

checkout_bp = Blueprint("checkout_bp", __name__)

# 申請対象として許可する item.status を一箇所で定義
CHECKOUT_ALLOWED_ITEM_STATUSES = ('入庫', '持ち出し中', '返却済')

@checkout_bp.route('/checkout_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper')
def checkout_request():
    db = get_db()

    # properユーザーリスト取得
    proper_users = get_proper_users(db)
    proper_usernames = [u['username'] for u in proper_users]
    proper_users_json = [
        {'username': u['username'], 'department': u.get('department', ''), 'realname': u.get('realname', '')}
        for u in proper_users
    ]
    manager_default = g.user['username']

    # partner + proper を所有者候補として統合
    partner_users = get_partner_users(db)
    tmp_map = {}
    for u in (proper_users + partner_users):
        tmp_map[u['username']] = {
            'username': u['username'],
            'department': u.get('department', ''),
            'realname': u.get('realname', '')
        }
    owner_candidates = list(tmp_map.values())

    # 申請画面表示
    if request.method == 'POST' and not request.form.get('action'):
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index_bp.index'))

        # ▼ item.status を確認し、許可ステータスのみを対象にする
        rows_status = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        allowed_ids = [str(r['id']) for r in rows_status if r['status'] in CHECKOUT_ALLOWED_ITEM_STATUSES]
        excluded_ids = [str(r['id']) for r in rows_status if r['status'] not in CHECKOUT_ALLOWED_ITEM_STATUSES]

        if not allowed_ids:
            flash(f"選択されたアイテムは申請対象の状態ではありません（許可: {', '.join(CHECKOUT_ALLOWED_ITEM_STATUSES)}）。")
            return redirect(url_for('index_bp.index'))

        if excluded_ids:
            flash(f"許可外の状態のアイテムを除外しました（ID: {', '.join(excluded_ids)}）。")

        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
            allowed_ids
        ).fetchall()
        items = [dict(row) for row in items]

        # --- 利用可能枝番の付与 ---
        placeholders = ','.join(['?'] * len(allowed_ids))
        child_rows = db.execute(
            f"SELECT item_id, branch_no, status FROM child_item WHERE item_id IN ({placeholders})",
            allowed_ids
        ).fetchall()

        eligible = defaultdict(list)
        for r in child_rows:
            if r['status'] not in ('破棄', '譲渡'):
                eligible[r['item_id']].append(r['branch_no'])

        for it in items:
            fallback_n = int(it.get('num_of_samples') or 1)
            fallback_branches = list(range(1, fallback_n + 1))
            branches = eligible.get(it['id'], fallback_branches)
            it['available_branches'] = sorted(branches)
            it['available_count'] = len(branches)

        items = [it for it in items if it['available_count'] > 0]
        if not items:
            flash("申請可能な枝番が存在しません（破棄・譲渡のみ）。")
            return redirect(url_for('index_bp.index'))

        # --- 承認者リスト整形 ---
        department = g.user['department']
        all_managers = get_managers_by_department(None, db)
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''

        # index と同じ realname 優先の表示名マップ
        user_rows = db.execute("""
            SELECT username,
                   COALESCE(NULLIF(realname, ''), username) AS display_name
            FROM users
        """).fetchall()
        user_display = {r["username"]: r["display_name"] for r in user_rows}

        return render_template(
            'checkout_form.html',
            items=items, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            proper_users=proper_users_json,
            owner_candidates=owner_candidates,
            manager_default=manager_default,
            user_display=user_display,
            g=g,
        )

    # 申請フォーム送信
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

        # ▼ 譲渡申請情報
        with_transfer = form.get("with_transfer") == "1"
        transfer_branch_ids = form.getlist("transfer_branch_ids") if with_transfer else []
        transfer_comment = form.get("transfer_comment", "") if with_transfer else ""
        transfer_date = form.get("transfer_date", "") if with_transfer else ""

        errors = []
        if not manager or manager not in proper_usernames:
            errors.append("管理者は候補から選択してください。")
        if not approver:
            errors.append("承認者を選択してください。")
        if len(qty_checked) != len(item_ids):
            errors.append("すべての数量チェックをしてください。")
        if with_transfer:
            if not transfer_branch_ids:
                errors.append("譲渡する枝番を選択してください。")
            if not transfer_comment.strip():
                errors.append("譲渡コメントを入力してください。")

        # ▼ item.status のバリデーション（入庫／持ち出し中／返却済 のみ許可）
        rows_status = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        not_allowed = [str(r['id']) for r in rows_status if r['status'] not in CHECKOUT_ALLOWED_ITEM_STATUSES]
        if not_allowed:
            errors.append(
                f"許可外の状態のアイテムが含まれています（ID: {', '.join(not_allowed)}）。"
                f"申請可能な状態は {', '.join(CHECKOUT_ALLOWED_ITEM_STATUSES)} です。"
            )

        if errors:
            # 再描画用：許可ステータスのみで items を作成
            allowed_ids = [str(r['id']) for r in rows_status if r['status'] in CHECKOUT_ALLOWED_ITEM_STATUSES]
            if allowed_ids:
                items = db.execute(
                    f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
                    allowed_ids
                ).fetchall()
            else:
                items = []
            items = [dict(row) for row in items]

            # 再描画用：利用可能枝番の付与
            if allowed_ids:
                placeholders = ','.join(['?'] * len(allowed_ids))
                child_rows = db.execute(
                    f"SELECT item_id, branch_no, status FROM child_item WHERE item_id IN ({placeholders})",
                    allowed_ids
                ).fetchall()
            else:
                child_rows = []

            eligible = defaultdict(list)
            for r in child_rows:
                if r['status'] not in ('破棄', '譲渡'):
                    eligible[r['item_id']].append(r['branch_no'])

            for it in items:
                fallback_n = int(it.get('num_of_samples') or 1)
                fallback_branches = list(range(1, fallback_n + 1))
                branches = eligible.get(it['id'], fallback_branches)
                it['available_branches'] = sorted(branches)
                it['available_count'] = len(branches)

            items = [it for it in items if it['available_count'] > 0]

            department = g.user['department']
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''
            error_dialog_message = " ".join(errors)
            return render_template(
                'checkout_form.html',
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
        new_status = "持ち出し譲渡申請中" if with_transfer else "持ち出し申請中"

        applicant = g.user['username']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_date = form.get('start_date', '')
        end_date = form.get('end_date', '')

        owner_lists = {}
        for id in item_ids:
            owner_lists[str(id)] = form.getlist(f'owner_list_{id}')

        for id in item_ids:
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()

            # ▼ 念押し：更新直前にも状態チェック（競合対策）
            if item['status'] not in CHECKOUT_ALLOWED_ITEM_STATUSES:
                # 許可外になっていたらこのIDだけスキップ（必要なら収集して後で通知も可）
                continue

            original_status = item['status']

            db.execute("UPDATE item SET status=? WHERE id=?", (new_status, id))

            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()
            new_values = dict(item)
            new_values['sample_manager'] = manager
            if with_transfer:
                new_values['status'] = "持ち出し譲渡申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])
                new_values['transfer_date'] = transfer_date
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
            else:
                new_values['status'] = "持ち出し申請中"
                new_values['checkout_start_date'] = start_date
                new_values['checkout_end_date'] = end_date
                new_values['owner_list'] = owner_lists.get(str(id), [])

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                id, json.dumps(new_values, ensure_ascii=False), applicant, comment, approver, "申請中", now_str, original_status
            ))

        db.commit()

        # ==== メール送信 ====
        changes = []
        all_owner_usernames = set()
        for id in item_ids:
            owners = owner_lists.get(str(id), [])
            owners = [o for o in owners if o]
            all_owner_usernames.update(owners)
            changes.append({
                "item_id": int(id),
                "manager": manager,
                "approver": approver,
                "applicant": applicant,
                "owners": owners
            })

        usernames_to_fetch = {approver, applicant, manager} | set(all_owner_usernames)
        profiles = get_user_profiles(db, list(usernames_to_fetch))

        approver_prof  = profiles.get(approver,  {})
        applicant_prof = profiles.get(applicant, {})
        manager_prof   = profiles.get(manager,   {})
        owner_profs    = [profiles[u] for u in sorted(all_owner_usernames)]

        to_emails = set()
        for p in [approver_prof, applicant_prof, manager_prof] + owner_profs:
            email = p.get("email") if isinstance(p, dict) else None
            if email:
                to_emails.add(email)
        to = ",".join(sorted(to_emails))

        subject = f"[申請] 持ち出し申請の保存（{len(changes)}件）"

        body = render_template(
            "mails/checkout_request.txt",
            approver_prof=approver_prof,
            applicant_prof=applicant_prof,
            manager_prof=manager_prof,
            owner_profs=owner_profs,
            changes=changes,
            profiles=profiles,
            with_transfer=with_transfer,
            start_date=start_date,
            end_date=end_date,
            comment=comment,
            transfer_comment=transfer_comment,
            transfer_date=transfer_date,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash("持ち出し申請を保存しました。承認待ちです。承認者ほか関係者にメールで連絡しました。")
        else:
            flash("持ち出し申請を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        return redirect(url_for('index_bp.index'))

    return redirect(url_for('index_bp.index'))
