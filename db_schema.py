# db_schema.py
import os
from werkzeug.security import generate_password_hash
from services import get_db, FIELDS, logger

# スキーマバージョンを 2 に引き上げ（users.last_login 追加）
SCHEMA_VERSION = 2

def _pragma(db):
    # SQLiteの推奨設定（必要に応じて調整）
    db.execute("PRAGMA foreign_keys = ON;")
    db.execute("PRAGMA journal_mode = WAL;")
    db.execute("PRAGMA synchronous = NORMAL;")

def _column_exists(db, table: str, column: str) -> bool:
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)

def init_db():
    """全テーブル作成（IF NOT EXISTS）。インデックスもこちらで。"""
    with get_db() as db:
        _pragma(db)
        db.execute("""
            CREATE TABLE IF NOT EXISTS db_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        db.execute(f'''
            CREATE TABLE IF NOT EXISTS item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {",".join([f"{f['key']} TEXT" for f in FIELDS])}
            )
        ''')
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT,
                email TEXT,
                department TEXT,
                realname TEXT,
                last_login TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER,
                role_id INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE,
                PRIMARY KEY(user_id, role_id)
            )
        """)
        db.execute('''
            CREATE TABLE IF NOT EXISTS child_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                branch_no INTEGER NOT NULL,
                owner TEXT NOT NULL,
                status TEXT NOT NULL,
                comment TEXT,
                transfer_dispose_date TEXT,
                UNIQUE(item_id, branch_no),
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS checkout_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                checkout_start_date TEXT NOT NULL,
                checkout_end_date TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS item_application (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                new_values TEXT NOT NULL,
                applicant TEXT NOT NULL,
                applicant_comment TEXT,
                approver TEXT,
                status TEXT NOT NULL,
                application_datetime TEXT NOT NULL,
                approval_datetime TEXT,
                approver_comment TEXT,
                original_status TEXT,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS application_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                applicant TEXT NOT NULL,
                application_content TEXT,
                applicant_comment TEXT,
                application_datetime TEXT NOT NULL,
                approver TEXT,
                approver_comment TEXT,
                approval_datetime TEXT,
                status TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS inventory_check (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                checker TEXT NOT NULL,
                comment TEXT,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
            )
        ''')

        # よく使うカラムにインデックス（任意だが体感改善しやすい）
        db.execute("CREATE INDEX IF NOT EXISTS idx_item_status ON item(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_child_item_item ON child_item(item_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_item_app_item ON item_application(item_id)")

        db.execute("INSERT OR REPLACE INTO db_meta(key, value) VALUES('schema_version', ?)",
                   (str(SCHEMA_VERSION),))
        db.commit()

def seed_minimal():
    """最低限のロールと管理者ユーザーの投入（冪等）。環境変数で上書き可。"""
    with get_db() as db:
        for role in ["admin", "manager", "proper", "partner"]:
            db.execute("INSERT OR IGNORE INTO roles (name) VALUES (?)", (role,))

        admin_username  = os.getenv("ADMIN_USERNAME", "admin")
        admin_password  = os.getenv("ADMIN_PASSWORD", "adminpass")
        admin_email     = os.getenv("ADMIN_EMAIL", "admin@example.com")
        admin_dept      = os.getenv("ADMIN_DEPARTMENT", "管理部門")
        admin_realname  = os.getenv("ADMIN_REALNAME", "管理者")

        row = db.execute("SELECT id FROM users WHERE username=?", (admin_username,)).fetchone()
        if row is None:
            hashed = generate_password_hash(admin_password)
            db.execute("""
                INSERT INTO users (username, password, email, department, realname)
                VALUES (?, ?, ?, ?, ?)
            """, (admin_username, hashed, admin_email, admin_dept, admin_realname))
            user_id = db.execute("SELECT id FROM users WHERE username=?",
                                 (admin_username,)).fetchone()['id']
            admin_role_id = db.execute("SELECT id FROM roles WHERE name='admin'").fetchone()['id']
            db.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                       (user_id, admin_role_id))
            logger.info("Created default admin user '%s'", admin_username)
        else:
            logger.info("Admin user '%s' already exists; skipping", admin_username)

        db.commit()

def get_version() -> int:
    with get_db() as db:
        try:
            row = db.execute("SELECT value FROM db_meta WHERE key='schema_version'").fetchone()
            return int(row['value']) if row else 0
        except Exception:
            return 0

def upgrade():
    """将来のマイグレーション用。v1→v2 で users.last_login を追加。"""
    with get_db() as db:
        _pragma(db)

        current = get_version()
        # v1 以前、もしくは安全側でカラムが無い場合に追加
        if current < 2 or not _column_exists(db, "users", "last_login"):
            # 既存DBに last_login が無ければ追加
            if not _column_exists(db, "users", "last_login"):
                db.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
                logger.info("Added users.last_login column")

            # スキーマバージョンを 2 に更新
            db.execute("INSERT OR REPLACE INTO db_meta(key, value) VALUES('schema_version', ?)",
                       (str(2),))

        db.commit()
