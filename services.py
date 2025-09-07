# services.py
import os
import json
import sqlite3
import logging
from functools import wraps
from datetime import datetime, timedelta, timezone
from flask import session, redirect, url_for, flash, g

# ===== logger（設定は app.py 側）=====
logger = logging.getLogger("myapp")

# ===== DB/フィールド定義 =====
DATABASE = 'items.db'

BASE_DIR = os.path.dirname(__file__)
FIELDS_PATH = os.path.join(BASE_DIR, 'fields.json')
with open(FIELDS_PATH, encoding='utf-8') as f:
    FIELDS = json.load(f)

USER_FIELDS  = [f for f in FIELDS if not f.get('internal')]
INDEX_FIELDS = [f for f in FIELDS if f.get('show_in_index')]
FIELD_KEYS   = [f['key'] for f in FIELDS]

SELECT_FIELD_PATH = os.path.join(BASE_DIR, 'select_fields.json')

def load_select_fields():
    if os.path.exists(SELECT_FIELD_PATH):
        with open(SELECT_FIELD_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_select_fields(data):
    with open(SELECT_FIELD_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== DB接続 =====
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# ===== ロック関連 =====
LOCK_TTL_MIN = 30

def _now():
    return datetime.now(timezone.utc)

def _is_lock_expired(locked_at_str):
    if not locked_at_str:
        return True
    try:
        t = datetime.fromisoformat(locked_at_str)
    except Exception:
        return True
    return (_now() - t) > timedelta(minutes=LOCK_TTL_MIN)

def acquire_locks(db, ids, username):
    blocked = []
    norm_ids = []
    for item_id in ids:
        try:
            item_id = int(item_id)
        except Exception:
            blocked.append(str(item_id)); continue
        norm_ids.append(item_id)
        row = db.execute("SELECT locked_by, locked_at FROM item WHERE id=?", (item_id,)).fetchone()
        if not row:
            blocked.append(str(item_id)); continue
        lb, la = row["locked_by"], row["locked_at"]
        if lb and not _is_lock_expired(la) and lb != username:
            blocked.append(str(item_id))
    if blocked:
        return False, blocked
    now = _now().isoformat()
    for item_id in norm_ids:
        db.execute("UPDATE item SET locked_by=?, locked_at=? WHERE id=?", (str(username or ""), now, item_id))
    db.commit()
    return True, []

def release_locks(db, ids):
    for item_id in ids:
        db.execute("UPDATE item SET locked_by=NULL, locked_at=NULL WHERE id=?", (item_id,))
    db.commit()

def _cleanup_expired_locks(db):
    rows = db.execute(
        "SELECT id, locked_at FROM item WHERE locked_by IS NOT NULL AND locked_by != ''"
    ).fetchall()
    expired_ids = [r["id"] for r in rows if _is_lock_expired(r["locked_at"])]
    if expired_ids:
        ph = ",".join(["?"]*len(expired_ids))
        db.execute(f"UPDATE item SET locked_by=NULL, locked_at=NULL WHERE id IN ({ph})", expired_ids)
        db.commit()

# ===== 認可/認証デコレータ =====
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def roles_required(*roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_roles = getattr(g, 'user_roles', [])
            if not any(role in user_roles for role in roles):
                flash('権限がありません')
                return redirect(url_for('index'))  # _urlfor_compat がテンプレで効いていればOK
            return func(*args, **kwargs)
        return wrapper
    return decorator
