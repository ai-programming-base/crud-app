# blueprints/dispose_transfer_request_bp.py
import json
from datetime import datetime
from collections import defaultdict

from flask import Blueprint, render_template, request, redirect, url_for, flash, g

from services import (
    get_db,
    login_required, roles_required,
    INDEX_FIELDS,
    get_managers_by_department,
    get_proper_users,
    get_user_profiles,
)

from send_mail import send_mail

dispose_transfer_request_bp = Blueprint("dispose_transfer_request_bp", __name__)

# 破棄・譲渡申請を受け付ける item.status を一箇所で定義
DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES = ('入庫', '持ち出し中', '返却済')

@dispose_transfer_request_bp.route('/dispose_transfer_request', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
def dispose_transfer_request():
    db = get_db()

    # 対応者（handler）候補：proper のみ
    proper_users = get_proper_users(db)  # [{'username','department','realname'},...]
    proper_users_json = [
        {'username': u['username'], 'department': u.get('department',''), 'realname': u.get('realname','')}
        for u in proper_users
    ]
    handler_default = g.user['username']

    # index と同じ realname 優先の表示名マップ
    user_rows = db.execute("""
        SELECT username,
            COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    #（所有者用：department + realname 形式／realname 空なら username）
    user_rows2 = db.execute("""
        SELECT
            username,
            TRIM(
                COALESCE(NULLIF(department,''),'') || ' ' ||
                COALESCE(NULLIF(realname,''), username)
            ) AS display_name
        FROM users
    """).fetchall()
    user_display_dept = {r["username"]: r["display_name"] for r in user_rows2}

    # POST: 申請フォーム送信
    if request.method == 'POST' and request.form.get('action') == 'submit':
        item_ids = request.form.getlist('item_id')
        dispose_type = request.form.get('dispose_type', '')
        handler = request.form.get('handler', '').strip()
        dispose_date = request.form.get("dispose_date", '')
        dispose_comment = request.form.get('dispose_comment', '').strip()
        applicant_comment = request.form.get('comment', '').strip()
        approver = request.form.get('approver', '').strip()
        target_child_ids = request.form.getlist('target_child_ids')
        qty_checked_ids = []
        for item_id in item_ids:
            if request.form.get(f'qty_checked_{item_id}'):
                qty_checked_ids.append(item_id)
        errors = []

        if not item_ids:
            errors.append("申請対象アイテムがありません。")
        if not dispose_type:
            errors.append("破棄か譲渡の種別を選択してください。")
        if not handler:
            errors.append("対応者を入力してください。")
        if not approver:
            errors.append("承認者を選択してください。")
        if not target_child_ids:
            errors.append("少なくとも1つの子アイテムを選択してください。")
        if len(qty_checked_ids) != len(item_ids):
            errors.append("すべての親アイテムで数量チェックをしてください。")

        # item.status を許可状態にバリデーション
        if item_ids:
            rows_status = db.execute(
                f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
                item_ids
            ).fetchall()
            not_allowed = [str(r['id']) for r in rows_status if r['status'] not in DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES]
            if not_allowed:
                errors.append(
                    f"許可外の状態のアイテムが含まれています（ID: {', '.join(not_allowed)}）。"
                    f"申請可能な状態は {', '.join(DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES)} です。"
                )

        if errors:
            # 再描画用：許可ステータスのみを対象にテーブルを復元
            allowed_ids = []
            if item_ids:
                allowed_ids = [str(r['id']) for r in rows_status if r['status'] in DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES]
            if allowed_ids:
                items = db.execute(
                    f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})", allowed_ids
                ).fetchall()
            else:
                items = []

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

            if allowed_ids:
                child_items = db.execute(
                    f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(allowed_ids))}) ORDER BY item_id, branch_no",
                    allowed_ids
                ).fetchall()
            else:
                child_items = []

            for msg in errors:
                flash(msg)

            department = g.user['department']
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''
            return render_template(
                'dispose_transfer_form.html',
                items=item_list, child_items=child_items, fields=INDEX_FIELDS,
                approver_default=approver_default,
                approver_list=sorted_managers,
                proper_users=proper_users_json,
                handler_default=handler_default,
                user_display=user_display,
                user_display_dept=user_display_dept
            )

        applicant = g.user['username']
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item_id in item_ids:
            # ▼ 念押し：更新直前にも状態チェック（競合で変化した場合の防止）
            cur = db.execute("SELECT * FROM item WHERE id=?", (item_id,)).fetchone()
            if not cur or cur['status'] not in DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES:
                continue

            item_dict = dict(cur)
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?", (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item_dict['sample_count'] = item_dict.get('num_of_samples', 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item_dict['sample_count'] = cnt

            new_values = dict(item_dict)
            new_values['dispose_type'] = dispose_type
            new_values['dispose_date'] = dispose_date
            new_values['handler'] = handler
            new_values['dispose_comment'] = dispose_comment

            target_child_branches_this = []
            for cid in target_child_ids:
                row = db.execute("SELECT item_id, branch_no FROM child_item WHERE id=?", (cid,)).fetchone()
                if row and row['item_id'] == int(item_id):
                    target_child_branches_this.append({"id": cid, "branch_no": row['branch_no']})

            new_values['target_child_branches'] = target_child_branches_this
            new_values['status'] = "破棄・譲渡申請中"

            original_status = cur['status']

            db.execute('''
                INSERT INTO item_application
                (item_id, new_values, applicant, applicant_comment, approver, status, application_datetime, original_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item_id, json.dumps(new_values, ensure_ascii=False), applicant,
                applicant_comment, approver, "申請中", now_str, original_status
            ))
            db.execute("UPDATE item SET status=? WHERE id=?", ("破棄・譲渡申請中", item_id))
        db.commit()

        # ==== メール送信部（承認者・申請者・管理者）====
        items_rows = db.execute(
            f"SELECT id, sample_manager FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        manager_usernames = {row['sample_manager'] for row in items_rows if row and row['sample_manager']}

        applicant = g.user['username']
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

        # 本文用：対象アイテムと枝番
        branches_by_item = defaultdict(list)
        if target_child_ids:
            rows = db.execute(
                f"SELECT id, item_id, branch_no FROM child_item WHERE id IN ({','.join(['?']*len(target_child_ids))})",
                target_child_ids
            ).fetchall()
            for r in rows:
                branches_by_item[r['item_id']].append(r['branch_no'])

        changes = []
        for row in items_rows:
            item_id = row['id']
            manager  = row['sample_manager']
            branch_nos = sorted(branches_by_item.get(item_id, []))
            changes.append({
                "item_id": item_id,
                "manager": manager,
                "branch_nos": branch_nos,
            })

        subject = f"[申請] 破棄・譲渡申請の保存（{len(changes)}件）"
        body = render_template(
            "mails/dispose_transfer_request.txt",
            approver_prof=approver_prof,
            applicant_prof=applicant_prof,
            manager_profs=manager_profs,
            changes=changes,
            profiles=profiles,
            dispose_type=dispose_type,
            dispose_date=dispose_date,
            handler=handler,
            dispose_comment=dispose_comment,
            applicant_comment=applicant_comment,
        )

        result = send_mail(to=to, subject=subject, body=body)
        if result:
            flash("破棄・譲渡申請を保存しました。承認待ちです。承認者ほか関係者にメールで連絡しました。")
        else:
            flash("破棄・譲渡申請を保存しました。承認待ちです。メール送信に失敗しましたので、関係者への連絡をお願いします。")
        # ==== ここまで ====

        return redirect(url_for('index_bp.index'))

    # 申請画面表示（POST/GET共通: item_idリストで遷移）
    if request.method == 'POST':
        item_ids = request.form.getlist('selected_ids')
        if not item_ids:
            flash("申請対象を選択してください")
            return redirect(url_for('index_bp.index'))

        # ▼ 追加：許可ステータスでフィルタ（入庫／持ち出し中／返却済のみ）
        rows_status = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        allowed_ids = [str(r['id']) for r in rows_status if r['status'] in DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES]
        excluded_ids = [str(r['id']) for r in rows_status if r['status'] not in DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES]

        if not allowed_ids:
            flash(f"選択されたアイテムは申請対象の状態ではありません（許可: {', '.join(DISPOSE_TRANSFER_ALLOWED_ITEM_STATUSES)}）。")
            return redirect(url_for('index_bp.index'))

        if excluded_ids:
            flash(f"許可外の状態のアイテムを除外しました（ID: {', '.join(excluded_ids)}）。")

        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})", allowed_ids
        ).fetchall()

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

        child_items = db.execute(
            f"SELECT * FROM child_item WHERE item_id IN ({','.join(['?']*len(allowed_ids))}) ORDER BY item_id, branch_no",
            allowed_ids
        ).fetchall()

        department = g.user['department']
        all_managers = get_managers_by_department(None, db)
        sorted_managers = (
            [m for m in all_managers if m['department'] == department] +
            [m for m in all_managers if m['department'] != department]
        )
        approver_default = sorted_managers[0]['username'] if sorted_managers else ''
        return render_template(
            'dispose_transfer_form.html',
            items=item_list, child_items=child_items, fields=INDEX_FIELDS,
            approver_default=approver_default,
            approver_list=sorted_managers,
            proper_users=proper_users_json,
            handler_default=handler_default,
            user_display=user_display,
            user_display_dept=user_display_dept
        )

    return redirect(url_for('index_bp.index'))
