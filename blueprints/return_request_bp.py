# blueprints/return_request_bp.py
import json
import re
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

# ▼ 返却申請を受け付ける item.status を一箇所で定義（後で変更したい場合はここだけ触ればOK）
RETURN_ALLOWED_ITEM_STATUSES = ('持ち出し中',)

STORAGE_PATTERN = re.compile(r"^S-\d{3}\s*上から([1-9]\d*)段目$")

def _is_valid_storage(text: str) -> bool:
    return bool(text and STORAGE_PATTERN.match(text))

@return_request_bp.route('/return_request', methods=['POST', 'GET'])
@login_required
@roles_required('admin', 'manager', 'proper', 'partner')
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

        # ▼ 許可ステータス（持ち出し中）でフィルタ
        rows_status = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        allowed_ids = [str(r['id']) for r in rows_status if r['status'] in RETURN_ALLOWED_ITEM_STATUSES]
        excluded_ids = [str(r['id']) for r in rows_status if r['status'] not in RETURN_ALLOWED_ITEM_STATUSES]

        if not allowed_ids:
            flash(f"選択されたアイテムは申請対象の状態ではありません（許可: {', '.join(RETURN_ALLOWED_ITEM_STATUSES)}）。")
            return redirect(url_for('index_bp.index'))

        if excluded_ids:
            flash(f"許可外の状態のアイテムを除外しました（ID: {', '.join(excluded_ids)}）。")

        items = db.execute(
            f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
            allowed_ids
        ).fetchall()

        # 元のロジック：サンプル数（破棄・譲渡を除いた枝番数）を計算
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
            # ▼ 初期表示用（テンプレで value に使う）
            storage='',
            return_date='',
            comment='',
            approver_selected=approver_default,
        )

    # 申請フォームからの送信時（GET, action=submit）
    if request.args.get('action') == 'submit':
        item_ids = request.args.getlist('item_id')
        checkeds = request.args.getlist('qty_checked')
        if not checkeds or len(checkeds) != len(item_ids):
            flash("全ての数量チェックを確認してください")
            return redirect(url_for('index_bp.index'))

        # ▼ サーバ側でも状態を再確認（競合対策）
        rows_status = db.execute(
            f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
            item_ids
        ).fetchall()
        not_allowed = [str(r['id']) for r in rows_status if r['status'] not in RETURN_ALLOWED_ITEM_STATUSES]
        if not_allowed:
            # 入力画面へ戻す（持ち出し中のみ残して再描画）
            flash(
                f"持ち出し中でないアイテム（ID: {', '.join(not_allowed)}）が含まれています。"
                f"申請可能な状態は {', '.join(RETURN_ALLOWED_ITEM_STATUSES)} です。"
            )
            allowed_ids = [str(r['id']) for r in rows_status if r['status'] in RETURN_ALLOWED_ITEM_STATUSES]
            if not allowed_ids:
                return redirect(url_for('index_bp.index'))

            # 再描画用 items 構築（元ロジックを踏襲）
            items = db.execute(
                f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
                allowed_ids
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

            department = g.user['department']
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''
            # エラーメッセージは flash で出しているのでそのまま再表示
            return render_template(
                'return_form.html',
                items=item_list, fields=INDEX_FIELDS,
                approver_default=approver_default,
                approver_list=sorted_managers,
                user_display=user_display,
                storage=request.args.get('storage',''),
                return_date=request.args.get('return_date',''),
                comment=request.args.get('comment',''),
                approver_selected=request.args.get('approver', approver_default),
            )
        applicant = g.user['username']
        applicant_comment = request.args.get('comment', '')
        approver = request.args.get('approver', '')
        return_date = request.args.get('return_date', datetime.now().strftime("%Y-%m-%d"))
        storage = request.args.get('storage', '')

        # ▼▼ 保管場所の形式チェック（サーバ側） ▼▼
        if not _is_valid_storage(storage):
            flash("保管場所は『S-123 上から3段目』の形式で入力してください（S-の後は3桁数字、段は1以上の整数）。")

            # 直前の許可判定を流用して再描画（持ち出し中のみ）
            rows_status = db.execute(
                f"SELECT id, status FROM item WHERE id IN ({','.join(['?']*len(item_ids))})",
                item_ids
            ).fetchall()
            allowed_ids = [str(r['id']) for r in rows_status if r['status'] in RETURN_ALLOWED_ITEM_STATUSES]
            if not allowed_ids:
                return redirect(url_for('index_bp.index'))

            items = db.execute(
                f"SELECT * FROM item WHERE id IN ({','.join(['?']*len(allowed_ids))})",
                allowed_ids
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

            department = g.user['department']
            all_managers = get_managers_by_department(None, db)
            sorted_managers = (
                [m for m in all_managers if m['department'] == department] +
                [m for m in all_managers if m['department'] != department]
            )
            approver_default = sorted_managers[0]['username'] if sorted_managers else ''

            # 入力値を保持して再表示
            return render_template(
                'return_form.html',
                items=item_list, fields=INDEX_FIELDS,
                approver_default=approver_default,
                approver_list=sorted_managers,
                user_display=user_display,
                storage=storage,
                return_date=return_date,
                comment=applicant_comment,
                approver_selected=approver or approver_default,
            )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for id in item_ids:
            item = db.execute("SELECT * FROM item WHERE id=?", (id,)).fetchone()

            # ▼ 念押し：更新直前にも状態チェック（競合で状態が変わっていたらスキップ）
            if item['status'] not in RETURN_ALLOWED_ITEM_STATUSES:
                continue

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
