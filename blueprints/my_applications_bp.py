# blueprints/my_applications_bp.py
from flask import Blueprint, render_template, request, g
from services import get_db, login_required

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
    elif status == "pending":
        where += " AND status NOT IN ('承認','差し戻し')"

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
