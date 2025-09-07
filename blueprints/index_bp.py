# blueprints/index_bp.py
from flask import Blueprint, render_template, request
from services import get_db, INDEX_FIELDS, login_required, logger, _cleanup_expired_locks

index_bp = Blueprint("index_bp", __name__)

@index_bp.route("/", endpoint="index")   # ← これで url_for('index') が使える
@login_required
def index():
    # ページ件数指定: ?per_page=10/20/50/100/all
    per_page_raw = request.args.get('per_page', '20')
    if per_page_raw == 'all':
        per_page = None
        page = 1
        offset = 0
    else:
        per_page = int(per_page_raw)
        page = int(request.args.get('page', 1))
        offset = (page - 1) * per_page

    db = get_db()
    _cleanup_expired_locks(db)

    user_rows = db.execute(
        """
        SELECT username,
               COALESCE(NULLIF(realname, ''), username) AS display_name
        FROM users
        """
    ).fetchall()
    user_display = {r["username"]: r["display_name"] for r in user_rows}

    logger.debug(user_display)

    # ===== フィルタ構築 =====
    filters = {}
    where = []
    params = []

    id_filter = request.args.get("id_filter", "").strip()
    filters["id"] = id_filter
    if id_filter:
        where.append("CAST(id AS TEXT) LIKE ?")
        params.append(f"%{id_filter}%")

    for f in INDEX_FIELDS:
        key = f["key"]
        v = request.args.get(f"{key}_filter", "").strip()
        filters[key] = v
        if v:
            where.append(f"{key} LIKE ?")
            params.append(f"%{v}%")

    sample_count_filter = request.args.get("sample_count_filter", "").strip()
    filters["sample_count"] = sample_count_filter

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    # ===== データ取得 =====
    if not sample_count_filter:
        total = db.execute(f"SELECT COUNT(*) FROM item {where_clause}", params).fetchone()[0]

        query = f"SELECT * FROM item {where_clause} ORDER BY id DESC"
        if per_page is not None:
            query += " LIMIT ? OFFSET ?"
            rows = db.execute(query, params + [per_page, offset]).fetchall()
        else:
            rows = db.execute(query, params).fetchall()

        item_list = []
        for row in rows:
            item = dict(row)
            item_id = item["id"]
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?",
                (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item["sample_count"] = item.get("num_of_samples", 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item["sample_count"] = cnt
            item_list.append(item)

    else:
        rows = db.execute(
            f"SELECT * FROM item {where_clause} ORDER BY id DESC",
            params
        ).fetchall()

        items_all = []
        for row in rows:
            item = dict(row)
            item_id = item["id"]
            child_total = db.execute(
                "SELECT COUNT(*) FROM child_item WHERE item_id=?",
                (item_id,)
            ).fetchone()[0]
            if child_total == 0:
                item["sample_count"] = item.get("num_of_samples", 0)
            else:
                cnt = db.execute(
                    "SELECT COUNT(*) FROM child_item WHERE item_id=? AND status NOT IN (?, ?)",
                    (item_id, "破棄", "譲渡")
                ).fetchone()[0]
                item["sample_count"] = cnt
            items_all.append(item)

        items_filtered = [it for it in items_all if str(it["sample_count"]) == sample_count_filter]

        total = len(items_filtered)
        if per_page is not None:
            item_list = items_filtered[offset:offset + per_page]
        else:
            item_list = items_filtered

    # ===== フィルタ候補の辞書 =====
    filter_choices_dict = {}

    for f in INDEX_FIELDS:
        col = f["key"]
        rows = db.execute(
            f"SELECT DISTINCT {col} FROM item WHERE {col} IS NOT NULL AND {col} != ''"
        ).fetchall()
        filter_choices_dict[col] = sorted({str(row[col]) for row in rows if row[col] not in (None, '')})

    id_rows = db.execute("SELECT id FROM item ORDER BY id DESC").fetchall()
    filter_choices_dict["id"] = [str(r["id"]) for r in id_rows]
    filter_choices_dict["sample_count"] = sorted({str(item["sample_count"]) for item in item_list})

    # ===== ページ数 =====
    if per_page is not None:
        page_count = max(1, (total + per_page - 1) // per_page)
    else:
        page_count = 1

    return render_template(
        'index.html',
        items=item_list,
        page=page,
        page_count=page_count,
        filters=filters,
        total=total,
        fields=INDEX_FIELDS,
        filter_choices_dict=filter_choices_dict,
        per_page=per_page_raw,
        user_display=user_display,
    )
