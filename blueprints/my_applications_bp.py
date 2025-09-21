# blueprints/my_applications_bp.py
from flask import Blueprint, render_template, request, g, flash, redirect, url_for
from datetime import datetime
import json

from services import get_db, login_required
from send_mail import send_mail
from blueprints.approval_bp import build_application_mail

my_applications_bp = Blueprint("my_applications_bp", __name__)


@my_applications_bp.route('/my_applications')
@login_required
def my_applications():
    status = request.args.get('status', 'all')
    db = get_db()
    params = [g.user['username']]
    where = "applicant=?"

    if status == "approved":
        where += " AND status='承認'"
    elif status == "remanded":
        where += " AND status='差し戻し'"
    elif status == "canceled":
        where += " AND status='取消'"
    elif status == "pending":
        where += " AND status NOT IN ('承認','差し戻し','取消')"

    apps = db.execute(f"""
        SELECT * FROM item_application
        WHERE {where}
        ORDER BY application_datetime DESC
    """, params).fetchall()

    # department realname 形式（realname が空なら username）
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
        'my_applications.html',
        applications=apps,
        status=status,
        user_display=user_display,
    )


@my_applications_bp.route('/applications/<int:app_id>/cancel', methods=['POST'])
@login_required
def cancel_application(app_id: int):
    """
    申請取り消し:
    - 申請者本人のみ実行可能
    - ステータスが「申請中」の場合のみ
    - アイテムを original_status に戻す
    - 履歴(application_history)に「取消」を追加
    - 関係者にメール送付
    """
    db = get_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = g.user['username']
    cancel_comment = (request.form.get('cancel_comment') or '').strip()

    row = db.execute("SELECT * FROM item_application WHERE id=?", (app_id,)).fetchone()
    if not row:
        flash("対象の申請が見つかりません。")
        return redirect(url_for('my_applications_bp.my_applications'))

    # sqlite3.Row → dict に変換
    app_row = dict(row)

    # 申請者チェック
    if app_row.get('applicant') != username:
        flash("この申請を取り消す権限がありません。")
        return redirect(url_for('my_applications_bp.my_applications'))

    # ステータスチェック
    if app_row.get('status') != '申請中':
        flash("申請中のもの以外は取り消せません。")
        return redirect(url_for('my_applications_bp.my_applications'))

    item_id = app_row.get('item_id')
    original_status = (app_row.get('original_status') or '').strip()

    # アイテムを元の状態に戻す
    if original_status:
        db.execute("UPDATE item SET status=? WHERE id=?", (original_status, item_id))

    # 申請レコードを更新（取消）
    db.execute('''
        UPDATE item_application
        SET approver_comment=?, approval_datetime=?, status=?
        WHERE id=?
    ''', (cancel_comment, now_str, '取消', app_id))

    # 履歴用に申請種別を推定
    try:
        new_vals = json.loads(app_row.get('new_values') or "{}")
    except Exception:
        new_vals = {}
    status = (new_vals.get('status') or '').strip()
    kind = (
        "入庫持ち出し譲渡申請" if status == "入庫持ち出し譲渡申請中" else
        "入庫持ち出し申請"     if status == "入庫持ち出し申請中"     else
        "入庫申請"             if status == "入庫申請中"             else
        "持ち出し譲渡申請"       if status == "持ち出し譲渡申請中"       else
        "持ち出し申請"           if status == "持ち出し申請中"           else
        "返却申請"              if status == "返却申請中"              else
        "破棄・譲渡申請"          if status == "破棄・譲渡申請中"          else
        ""
    )
    application_content_for_history = app_row.get('application_content') or kind or (app_row.get('new_values') or "")

    # 履歴に記録
    db.execute('''
        INSERT INTO application_history
        (item_id, applicant, application_content, applicant_comment, application_datetime,
         approver, status, approval_datetime, approver_comment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        item_id,
        app_row.get('applicant'),
        application_content_for_history,
        app_row.get('applicant_comment'),
        app_row.get('application_datetime'),
        app_row.get('approver'),
        '取消',
        now_str,
        cancel_comment
    ))

    # メール送信
    to, subject, body = build_application_mail(db, app_row, action="cancel", approver_comment=cancel_comment)
    sent = send_mail(to=to, subject=subject, body=body)

    db.commit()

    if sent:
        flash("申請を取り消しました。関係者にメールで連絡しました。")
    else:
        flash("申請を取り消しました。メール送信に失敗しましたので、関係者への連絡をお願いします。")

    return redirect(url_for('my_applications_bp.my_applications'))
