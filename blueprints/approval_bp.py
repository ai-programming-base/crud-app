# blueprints/approval_bp.py
from flask import Blueprint, render_template, request, flash, g
from flask import url_for, redirect
from datetime import datetime
import json

from services import (
    get_db, FIELD_KEYS, INDEX_FIELDS,
    get_user_profiles, login_required, roles_required
)
from send_mail import send_mail

approval_bp = Blueprint("approval_bp", __name__)

def build_application_mail(db, app_row, action: str, approver_comment: str = ""):
    """
    item_application の1件 (app_row) と action ('approve' | 'reject') から
    (to, subject, body) を返す。
    """
    # sqlite3.Row は .get を持たないため dict 化
    if not isinstance(app_row, dict):
        app_row = dict(app_row)

    item_id = app_row["item_id"]
    approver = app_row["approver"]
    applicant = app_row["applicant"]
    try:
        new_values = json.loads(app_row.get("new_values") or "{}")
    except Exception:
        new_values = {}
    status = (new_values.get("status") or "").strip()

    manager_username = (new_values.get("sample_manager") or "").strip()
    if not manager_username:
        row = db.execute("SELECT sample_manager FROM item WHERE id=?", (item_id,)).fetchone()
        manager_username = (row["sample_manager"] if row and row["sample_manager"] else "").strip()

    include_owners = status in (
        "入庫持ち出し申請中", "入庫持ち出し譲渡申請中",
        "持ち出し申請中", "持ち出し譲渡申請中"
    )
    owners = list(new_values.get("owner_list") or []) if include_owners else []

    usernames = {approver, applicant}
    if manager_username:
        usernames.add(manager_username)
    if include_owners:
        usernames |= set([u for u in owners if u])

    profiles = get_user_profiles(db, list(usernames))
    approver_prof  = profiles.get(approver,  {})
    applicant_prof = profiles.get(applicant, {})
    manager_prof   = profiles.get(manager_username, {}) if manager_username else {}
    owner_profs    = [profiles[u] for u in sorted(set([u for u in owners if u]))] if include_owners else []

    to_emails = set()
    for p in [approver_prof, applicant_prof, manager_prof] + owner_profs:
        if isinstance(p, dict) and p.get("email"):
            to_emails.add(p["email"])
    to = ",".join(sorted(to_emails))

    action_label = "承認" if action == "approve" else "差し戻し"
    kind = (
        "入庫持ち出し譲渡申請" if status == "入庫持ち出し譲渡申請中" else
        "入庫持ち出し申請"     if status == "入庫持ち出し申請中" else
        "入庫申請"             if status == "入庫申請中" else
        "持ち出し譲渡申請"       if status == "持ち出し譲渡申請中" else
        "持ち出し申請"           if status == "持ち出し申請中" else
        "返却申請"              if status == "返却申請中" else
        "破棄・譲渡申請"          if status == "破棄・譲渡申請中" else
        (status or "申請")
    )
    subject = f"[{action_label}] {kind}（ID: {item_id}）"

    changes = [{
        "item_id": item_id,
        "manager": manager_username,
        "owners": owners
    }]

    body = render_template(
        "mails/approval_result.txt",
        approver_prof=approver_prof,
        applicant_prof=applicant_prof,
        manager_prof=manager_prof,
        owner_profs=owner_profs,
        profiles=profiles,
        action_label=action_label,
        kind=kind,
        changes=changes,
        start_date=new_values.get("checkout_start_date", ""),
        end_date=new_values.get("checkout_end_date", ""),
        transfer_date=new_values.get("transfer_date", ""),
        transfer_comment=new_values.get("transfer_comment", ""),
        dispose_type=new_values.get("dispose_type", ""),
        dispose_date=new_values.get("dispose_date", ""),
        dispose_comment=new_values.get("dispose_comment", ""),
        return_date=new_values.get("return_date", ""),
        storage=new_values.get("storage", ""),
        applicant_comment=app_row.get("applicant_comment", "") or "",
        approver_comment=approver_comment or "",
    )

    return to, subject, body

@approval_bp.route('/approval', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'manager')
def approval():
    db = get_db()
    username = g.user['username']

    # 承認対象の取得＋new_valuesをパース＆所有者表示用枝番割当
    items_raw = db.execute(
        "SELECT * FROM item_application WHERE approver=? AND status=? ORDER BY application_datetime DESC",
        (username, "申請中")
    ).fetchall()
    parsed_items = []
    for item in items_raw:
        parsed = dict(item)
        try:
            parsed['parsed_values'] = json.loads(item['new_values'])
        except Exception:
            parsed['parsed_values'] = {}

        # 所有者入力がある申請のプレビュー用：生きている枝番と再利用・追加割当
        pv = parsed.get('parsed_values', {})
        owners = pv.get('owner_list') or []
        if owners:
            item_id = parsed['item_id']
            rows = db.execute(
                "SELECT branch_no, status FROM child_item WHERE item_id=?",
                (item_id,)
            ).fetchall()
            disposed_transferred = {r['branch_no'] for r in rows if r['status'] in ('破棄', '譲渡')}
            occupied_alive = {r['branch_no'] for r in rows if r['status'] not in ('破棄', '譲渡')}

            owner_pairs = []
            reuse_candidates = sorted(occupied_alive)
            reuse_iter = iter(reuse_candidates)
            max_existing = max([0] + [r['branch_no'] for r in rows])
            next_branch = max_existing + 1

            for owner in owners:
                try:
                    b = next(reuse_iter)
                except StopIteration:
                    while next_branch in disposed_transferred:
                        next_branch += 1
                    b = next_branch
                    next_branch += 1
                owner_pairs.append((b, owner))
            parsed['owner_pairs'] = owner_pairs
        else:
            parsed['owner_pairs'] = []

        parsed_items.append(parsed)
    items = parsed_items

    # 表示名マップ（index と同様：部署 + 氏名（なければ username））
    user_rows = db.execute("""
        SELECT
            username,
            TRIM(
                COALESCE(NULLIF(department,''),'') || ' ' ||
                COALESCE(NULLIF(realname,''), username)
            ) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    if request.method == 'POST':
        selected_ids = request.form.getlist('selected_ids')
        comment = request.form.get('approve_comment', '').strip()
        action = request.form.get('action')
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not selected_ids:
            flash("対象を選択してください")
            items_raw = db.execute(
                "SELECT * FROM item_application WHERE approver=? AND status=? ORDER BY application_datetime DESC",
                (username, "申請中")
            ).fetchall()
            parsed_items = []
            for item in items_raw:
                parsed = dict(item)
                try:
                    parsed['parsed_values'] = json.loads(item['new_values'])
                except Exception:
                    parsed['parsed_values'] = {}
                parsed_items.append(parsed)
            return render_template('approval.html', items=parsed_items, fields=INDEX_FIELDS, user_display=user_display)

        for app_id in selected_ids:
            app_row = db.execute("SELECT * FROM item_application WHERE id=?", (app_id,)).fetchone()
            if not app_row:
                continue
            app_row = dict(app_row)  # sqlite3.Row → dict に変換（▼ 追加：.get エラー対策）

            item_id = app_row['item_id']
            try:
                new_values = json.loads(app_row['new_values'] or "{}")
            except Exception:
                new_values = {}
            status = new_values.get('status')

            if action == 'approve':
                item_columns = set(FIELD_KEYS)
                # new_values の一括反映から status を除外（状態は下の分岐でのみ更新）
                filtered_values = {k: v for k, v in new_values.items()
                                   if k in item_columns and k not in ('status',)}
                set_clause = ", ".join([f"{k}=?" for k in filtered_values.keys()])
                params = [filtered_values[k] for k in filtered_values.keys()]
                if set_clause:
                    db.execute(
                        f'UPDATE item SET {set_clause} WHERE id=?',
                        params + [item_id]
                    )

                # 入庫系列の承認者部門を approval_group に記録
                approver_dept = (g.user['department'] or "").strip()
                if status in ("入庫持ち出し譲渡申請中", "入庫持ち出し申請中", "入庫申請中"):
                    db.execute("UPDATE item SET approval_group=? WHERE id=?", (approver_dept, item_id))

                # ▼ 各申請種別ごとの状態遷移（ここでのみ status を更新）
                if status == "入庫持ち出し譲渡申請中":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))

                    start_date = new_values.get("checkout_start_date", "")
                    end_date = new_values.get("checkout_end_date", "")
                    owners = new_values.get("owner_list", [])
                    num_of_samples = int(new_values.get("num_of_samples", 1))
                    if not owners:
                        owner = new_values.get("sample_manager", "")
                        owners = [owner] * num_of_samples

                    for idx, owner in enumerate(owners, 1):
                        child_item = db.execute(
                            "SELECT * FROM child_item WHERE item_id=? AND branch_no=?", (item_id, idx)
                        ).fetchone()
                        if not child_item:
                            db.execute(
                                "INSERT INTO child_item (item_id, branch_no, owner, status, comment) VALUES (?, ?, ?, ?, ?)",
                                (item_id, idx, owner, "持ち出し中", "")
                            )
                        else:
                            db.execute(
                                "UPDATE child_item SET owner=?, status=? WHERE item_id=? AND branch_no=?",
                                (owner, "持ち出し中", item_id, idx)
                            )

                    db.execute(
                        "INSERT INTO checkout_history (item_id, checkout_start_date, checkout_end_date) VALUES (?, ?, ?)",
                        (item_id, start_date, end_date)
                    )

                    transfer_branch_nos = new_values.get("transfer_branch_nos", [])
                    transfer_date = new_values.get("transfer_date", "")
                    transfer_comment = new_values.get("transfer_comment", "")
                    for branch_no in transfer_branch_nos:
                        db.execute(
                            "UPDATE child_item SET status=?, comment=?, owner=?, transfer_dispose_date=? WHERE item_id=? AND branch_no=?",
                            ("譲渡", transfer_comment, '', transfer_date, item_id, branch_no)
                        )

                elif status == "入庫持ち出し申請中":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))
                    start_date = new_values.get("checkout_start_date", "")
                    end_date = new_values.get("checkout_end_date", "")
                    owners = new_values.get("owner_list", [])
                    if not owners:
                        num_of_samples = int(new_values.get("num_of_samples", 1))
                        owner = new_values.get("sample_manager", "")
                        owners = [owner] * num_of_samples
                    for idx, owner in enumerate(owners, 1):
                        child_item = db.execute(
                            "SELECT * FROM child_item WHERE item_id=? AND branch_no=?", (item_id, idx)
                        ).fetchone()
                        if not child_item:
                            db.execute(
                                "INSERT INTO child_item (item_id, branch_no, owner, status, comment) VALUES (?, ?, ?, ?, ?)",
                                (item_id, idx, owner, "持ち出し中", "")
                            )
                        else:
                            db.execute(
                                "UPDATE child_item SET owner=?, status=? WHERE item_id=? AND branch_no=?",
                                (owner, "持ち出し中", item_id, idx)
                            )

                    db.execute(
                        "INSERT INTO checkout_history (item_id, checkout_start_date, checkout_end_date) VALUES (?, ?, ?)",
                        (item_id, start_date, end_date)
                    )

                elif status == "入庫申請中":
                    db.execute("UPDATE item SET status=? WHERE id=?", ("入庫", item_id))

                elif status in ("持ち出し申請中", "持ち出し譲渡申請中"):
                    db.execute("UPDATE item SET status=? WHERE id=?", ("持ち出し中", item_id))
                    start_date = new_values.get("checkout_start_date", "")
                    end_date = new_values.get("checkout_end_date", "")

                    owners = new_values.get("owner_list", [])
                    if not owners:
                        num_of_samples = int(new_values.get("num_of_samples", 1))
                        owner_fallback = new_values.get("sample_manager", "")
                        owners = [owner_fallback] * num_of_samples

                    rows = db.execute(
                        "SELECT branch_no, status FROM child_item WHERE item_id=? ORDER BY branch_no",
                        (item_id,)
                    ).fetchall()

                    if not rows:
                        # 子アイテムが未作成なら 1..n を作成
                        for idx, owner in enumerate(owners, 1):
                            db.execute(
                                "INSERT INTO child_item (item_id, branch_no, owner, status, comment) VALUES (?, ?, ?, ?, ?)",
                                (item_id, idx, owner, "持ち出し中", "")
                            )
                    else:
                        # 生存枝番の所有者だけ更新（破棄・譲渡は保持）
                        alive = [r["branch_no"] for r in rows if r["status"] not in ("破棄", "譲渡")]
                        if len(owners) > len(alive):
                            flash(f"通し番号 {item_id}: 生きている枝番（{len(alive)}）より所有者が多い（{len(owners)}）ため、追加は行わず更新できません。")
                        else:
                            for branch_no, owner in zip(alive, owners):
                                db.execute(
                                    """
                                    UPDATE child_item
                                    SET owner  = CASE WHEN status IN (?, ?) THEN owner ELSE ? END,
                                        status = CASE WHEN status IN (?, ?) THEN status ELSE ? END
                                    WHERE item_id=? AND branch_no=?
                                    """,
                                    ("破棄", "譲渡", owner, "破棄", "譲渡", "持ち出し中", item_id, branch_no)
                                )

                    db.execute(
                        "INSERT INTO checkout_history (item_id, checkout_start_date, checkout_end_date) VALUES (?, ?, ?)",
                        (item_id, start_date, end_date)
                    )

                    if status == "持ち出し譲渡申請中":
                        transfer_branch_nos = new_values.get("transfer_branch_nos", [])
                        transfer_date = new_values.get("transfer_date", "")
                        transfer_comment = new_values.get("transfer_comment", "")
                        for branch_no in transfer_branch_nos:
                            db.execute(
                                "UPDATE child_item SET status=?, comment=?, owner=?, transfer_dispose_date=? WHERE item_id=? AND branch_no=?",
                                ("譲渡", transfer_comment, '', transfer_date, item_id, branch_no)
                            )

                elif status == "返却申請中":
                    db.execute(
                        "UPDATE item SET status=?, storage=? WHERE id=?",
                        ("入庫", new_values.get("storage", ""), item_id)
                    )
                    db.execute(
                        """
                        UPDATE child_item
                        SET status=?, owner=?
                        WHERE item_id=? AND status NOT IN (?, ?)
                        """,
                        ("返却済", '', item_id, "破棄", "譲渡")
                    )

                elif status == "破棄・譲渡申請中":
                    dispose_type = new_values.get('dispose_type')
                    target_child_branches = new_values.get('target_child_branches', [])
                    transfer_dispose_date = new_values.get("dispose_date", "")
                    dispose_comment = new_values.get('dispose_comment', '')

                    # 子アイテムの状態更新（破棄 or 譲渡）
                    new_status = "破棄" if dispose_type == "破棄" else "譲渡"
                    for target in target_child_branches:
                        cid = target["id"]
                        db.execute(
                            "UPDATE child_item SET status=?, comment=?, owner=?, transfer_dispose_date=?  WHERE id=?",
                            (new_status, dispose_comment, '', transfer_dispose_date, cid)
                        )

                    # 親アイテムの status は「元の状態」に戻す（元仕様の維持）
                    original_status = app_row.get('original_status') or ""
                    if original_status:
                        db.execute("UPDATE item SET status=? WHERE id=?", (original_status, item_id))
                    # original_status が無い場合は親の現行状態は変更しない（安全策）

                # 申請レコードの状態更新＆履歴記録
                db.execute('''
                    UPDATE item_application SET
                        approver_comment=?, approval_datetime=?, status=?
                    WHERE id=?
                ''', (comment, now_str, "承認", app_id))

                db.execute('''
                    INSERT INTO application_history
                    (item_id, applicant, application_content, applicant_comment, application_datetime, approver, status, approval_datetime, approver_comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    item_id, app_row['applicant'],
                    "入庫持ち出し譲渡申請" if status == "入庫持ち出し譲渡申請中"
                    else "持ち出し譲渡申請" if status == "持ち出し譲渡申請中"
                    else "破棄申請" if status == "破棄・譲渡申請中" and new_values.get("dispose_type") == "破棄"
                    else "譲渡申請" if status == "破棄・譲渡申請中" and new_values.get("dispose_type") == "譲渡"
                    else "入庫申請" if status == "入庫申請中"
                    else "入庫持ち出し申請" if status == "入庫持ち出し申請中"
                    else "持ち出し申請" if status == "持ち出し申請中"
                    else "持ち出し終了申請" if status == "返却申請中"
                    else (app_row['new_values'] or status or ""),
                    app_row['applicant_comment'], app_row['application_datetime'], app_row['approver'],
                    "承認", now_str, comment
                ))

                # メール送信
                to, subject, body = build_application_mail(db, app_row, action="approve", approver_comment=comment)
                result = send_mail(to=to, subject=subject, body=body)
                if result:
                    flash("承認しました。関係者にメールで連絡しました。")
                else:
                    flash("承認しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

            elif action == 'reject':
                original_status = app_row.get('original_status')
                if original_status:
                    db.execute("UPDATE item SET status=? WHERE id=?", (original_status, item_id))
                db.execute(
                    '''
                    UPDATE item_application SET
                        approver_comment=?, approval_datetime=?, status=?
                    WHERE id=?
                    ''',
                    (comment, now_str, "差し戻し", app_id)
                )

                to, subject, body = build_application_mail(db, app_row, action="reject", approver_comment=comment)
                result = send_mail(to=to, subject=subject, body=body)
                if result:
                    flash("差し戻しました。関係者にメールで連絡しました。")
                else:
                    flash("差し戻しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

        db.commit()
        return render_template('approval.html', items=[], fields=INDEX_FIELDS, message="処理が完了しました", finish=True)

    return render_template('approval.html', items=items, fields=INDEX_FIELDS, user_display=user_display)


@approval_bp.route('/my_approvals')
@login_required
@roles_required('admin', 'manager')
def my_approvals():
    db = get_db()
    username = g.user['username']
    status = request.args.get('status', 'all')  # 'all' | '申請中' | '承認' | '差し戻し'

    where = "approver=?"
    params = [username]
    if status in ("申請中", "承認", "差し戻し"):
        where += " AND status=?"
        params.append(status)

    rows = db.execute(f"""
        SELECT *
        FROM item_application
        WHERE {where}
        ORDER BY application_datetime DESC
    """, params).fetchall()

    # 申請詳細プレビュー用（approval.html 相当）：new_values パース＆所有者プレビュー
    items = []
    for r in rows:
        d = dict(r)
        try:
            d['parsed_values'] = json.loads(d.get('new_values') or "{}")
        except Exception:
            d['parsed_values'] = {}

        # 所有者プレビュー（approval() と同等の概略）
        pv = d.get('parsed_values', {})
        owners = pv.get('owner_list') or []
        if owners:
            item_id = d['item_id']
            ci = db.execute(
                "SELECT branch_no, status FROM child_item WHERE item_id=?",
                (item_id,)
            ).fetchall()
            disposed_transferred = {x['branch_no'] for x in ci if x['status'] in ('破棄', '譲渡')}
            alive = {x['branch_no'] for x in ci if x['status'] not in ('破棄', '譲渡')}
            reuse_iter = iter(sorted(alive))
            max_existing = max([0] + [x['branch_no'] for x in ci])
            next_branch = max_existing + 1

            owner_pairs = []
            for owner in owners:
                try:
                    b = next(reuse_iter)
                except StopIteration:
                    while next_branch in disposed_transferred:
                        next_branch += 1
                    b = next_branch
                    next_branch += 1
                owner_pairs.append((b, owner))
            d['owner_pairs'] = owner_pairs
        else:
            d['owner_pairs'] = []

        items.append(d)

    # 表示名（部署+氏名 or username）マップ
    user_rows = db.execute("""
        SELECT
            username,
            TRIM(
                COALESCE(NULLIF(department,''),'') || ' ' ||
                COALESCE(NULLIF(realname,''), username)
            ) AS display_name
        FROM users
    """).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    return render_template(
        'my_approvals.html',
        items=items,
        fields=INDEX_FIELDS,
        user_display=user_display,
        cur_status=status
    )
