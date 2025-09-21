# db_schema.py
import os
from werkzeug.security import generate_password_hash
from services import get_db, FIELDS, logger

# =========================
# スキーマバージョン
# v3: 参照整備（CASCADE/SET NULL の是正）と孤児掃除
# =========================
SCHEMA_VERSION = 3


# --------- 内部ユーティリティ ---------
def _pragma(db):
    """SQLite 推奨設定。外部キー有効化は get_db() 側でも実施しているが念のため。"""
    db.execute("PRAGMA foreign_keys = ON;")
    db.execute("PRAGMA journal_mode = WAL;")
    db.execute("PRAGMA synchronous = NORMAL;")


def _column_exists(db, table: str, column: str) -> bool:
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _fk_list(db, table: str):
    """テーブルの外部キー定義一覧を返す。"""
    return db.execute(f"PRAGMA foreign_key_list({table})").fetchall()


def _need_recreate_on_delete(db, table: str, ref_table: str, expected: str) -> bool:
    """
    該当テーブルの ref_table への FK の on_delete が expected（CASCADE/SET NULL）と一致しない場合 True。
    SQLite は ALTER で on_delete を変えられないため再作成が必要。
    """
    fks = _fk_list(db, table)
    for fk in fks:
        if fk["table"] == ref_table:
            if (fk["on_delete"] or "").upper() != expected.upper():
                return True
    return False


def _recreate_table(db, table: str, create_sql: str, copy_cols: list[str], post_sql: list[str] = None):
    """
    SQLite 流のテーブル再作成:
      1) RENAME → 2) 新規 CREATE → 3) 交差カラムを INSERT SELECT → 4) 旧 DROP → 5) 追加処理
    """
    post_sql = post_sql or []
    logger.info("Recreating table: %s", table)

    # 旧テーブルの実在確認（初回作成のケースに備えて分岐）
    exists = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None

    db.execute("PRAGMA foreign_keys=OFF;")
    if exists:
        db.execute(f"ALTER TABLE {table} RENAME TO {table}_old;")
    db.execute(create_sql)

    if exists:
        old_cols = [r["name"] for r in db.execute(f"PRAGMA table_info({table}_old)").fetchall()]
        cols = [c for c in copy_cols if c in old_cols]
        if cols:
            col_list = ",".join(cols)
            db.execute(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM {table}_old;")
        db.execute(f"DROP TABLE {table}_old;")

    for sql in post_sql:
        db.execute(sql)
    db.execute("PRAGMA foreign_keys=ON;")


# --------- 初期作成 ---------
def init_db():
    """全テーブル作成（IF NOT EXISTS）。インデックスもこちらで。"""
    with get_db() as db:
        _pragma(db)

        # メタ
        db.execute("""
            CREATE TABLE IF NOT EXISTS db_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # item（フィールドは fields.json に準拠）
        db.execute(f'''
            CREATE TABLE IF NOT EXISTS item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {",".join([f"{f['key']} TEXT" for f in FIELDS])}
            )
        ''')

        # ユーザー/ロール
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT UNIQUE NOT NULL,
                password   TEXT,
                email      TEXT,
                department TEXT,
                realname   TEXT,
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

        # 実体・明細系（item が消えたら一緒に消える）
        db.execute('''
            CREATE TABLE IF NOT EXISTS child_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id  INTEGER NOT NULL,
                branch_no INTEGER NOT NULL,
                owner    TEXT NOT NULL,
                status   TEXT NOT NULL,
                comment  TEXT,
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
                checkout_end_date   TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS inventory_check (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id   INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                checker    TEXT NOT NULL,
                comment    TEXT,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
            )
        ''')

        # 申請・履歴（監査ログは残す）→ ON DELETE SET NULL
        db.execute('''
            CREATE TABLE IF NOT EXISTS item_application (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER, -- NULL 可
                new_values         TEXT NOT NULL,
                applicant          TEXT NOT NULL,
                applicant_comment  TEXT,
                approver           TEXT,
                status             TEXT NOT NULL,
                application_datetime TEXT NOT NULL,
                approval_datetime    TEXT,
                approver_comment     TEXT,
                original_status      TEXT,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE SET NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS application_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER, -- NULL 可
                applicant           TEXT NOT NULL,
                application_content TEXT,
                applicant_comment   TEXT,
                application_datetime TEXT NOT NULL,
                approver            TEXT,
                approver_comment    TEXT,
                approval_datetime   TEXT,
                status              TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE SET NULL
            )
        ''')

        # よく使うカラムにインデックス
        db.execute("CREATE INDEX IF NOT EXISTS idx_item_status       ON item(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_child_item_item   ON child_item(item_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_checkout_item     ON checkout_history(item_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_inventory_item    ON inventory_check(item_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_item_app_item     ON item_application(item_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_app_hist_item     ON application_history(item_id)")

        # バージョン書き込み
        db.execute("INSERT OR REPLACE INTO db_meta(key, value) VALUES('schema_version', ?)",
                   (str(SCHEMA_VERSION),))
        db.commit()


# --------- シード ---------
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


# --------- バージョン管理 ---------
def get_version() -> int:
    with get_db() as db:
        try:
            row = db.execute("SELECT value FROM db_meta WHERE key='schema_version'").fetchone()
            return int(row['value']) if row else 0
        except Exception:
            return 0

def upgrade():
    """
    将来のマイグレーション用。
    v2→v3:
      - child_item / checkout_history / inventory_check を ON DELETE CASCADE へ是正（必要時再作成）
      - item_application / application_history を ON DELETE SET NULL へ是正（必要時再作成）
      - 過去DBで発生した孤児の掃除
    """
    with get_db() as db:
        _pragma(db)

        # ★ 重要: 空DBや古いDBでも動くように、まず db_meta を必ず用意
        db.execute("""
            CREATE TABLE IF NOT EXISTS db_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        current = get_version()
        if current >= 3:
            return  # 何もしない

        # --- v3 マイグレーション本体 ---
        # 1) 明細/実体系は CASCADE
        if _need_recreate_on_delete(db, "child_item", "item", "CASCADE"):
            _recreate_table(
                db,
                "child_item",
                '''
                CREATE TABLE child_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id  INTEGER NOT NULL,
                    branch_no INTEGER NOT NULL,
                    owner    TEXT NOT NULL,
                    status   TEXT NOT NULL,
                    comment  TEXT,
                    transfer_dispose_date TEXT,
                    UNIQUE(item_id, branch_no),
                    FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
                )
                ''',
                copy_cols=["id","item_id","branch_no","owner","status","comment","transfer_dispose_date"],
                post_sql=[
                    "CREATE INDEX IF NOT EXISTS idx_child_item_item ON child_item(item_id)"
                ]
            )

        if _need_recreate_on_delete(db, "checkout_history", "item", "CASCADE"):
            _recreate_table(
                db,
                "checkout_history",
                '''
                CREATE TABLE checkout_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    checkout_start_date TEXT NOT NULL,
                    checkout_end_date   TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
                )
                ''',
                copy_cols=["id","item_id","checkout_start_date","checkout_end_date"],
                post_sql=[
                    "CREATE INDEX IF NOT EXISTS idx_checkout_item ON checkout_history(item_id)"
                ]
            )

        if _need_recreate_on_delete(db, "inventory_check", "item", "CASCADE"):
            _recreate_table(
                db,
                "inventory_check",
                '''
                CREATE TABLE inventory_check (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id   INTEGER NOT NULL,
                    checked_at TEXT NOT NULL,
                    checker    TEXT NOT NULL,
                    comment    TEXT,
                    FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE CASCADE
                )
                ''',
                copy_cols=["id","item_id","checked_at","checker","comment"],
                post_sql=[
                    "CREATE INDEX IF NOT EXISTS idx_inventory_item ON inventory_check(item_id)"
                ]
            )

        # 2) ログ系は SET NULL
        if _need_recreate_on_delete(db, "item_application", "item", "SET NULL"):
            _recreate_table(
                db,
                "item_application",
                '''
                CREATE TABLE item_application (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    new_values           TEXT NOT NULL,
                    applicant            TEXT NOT NULL,
                    applicant_comment    TEXT,
                    approver             TEXT,
                    status               TEXT NOT NULL,
                    application_datetime TEXT NOT NULL,
                    approval_datetime    TEXT,
                    approver_comment     TEXT,
                    original_status      TEXT,
                    FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE SET NULL
                )
                ''',
                copy_cols=["id","item_id","new_values","applicant","applicant_comment","approver","status",
                           "application_datetime","approval_datetime","approver_comment","original_status"],
                post_sql=[
                    "CREATE INDEX IF NOT EXISTS idx_item_app_item ON item_application(item_id)"
                ]
            )

        # application_history: on_delete と NOT NULL の両面チェック
        need_hist_recreate = False
        for fk in _fk_list(db, "application_history"):
            if fk["table"] == "item":
                if (fk["on_delete"] or "").upper() != "SET NULL":
                    need_hist_recreate = True
        cols = db.execute("PRAGMA table_info(application_history)").fetchall()
        for c in cols:
            if c["name"] == "item_id" and c["notnull"] == 1:
                need_hist_recreate = True

        if need_hist_recreate:
            _recreate_table(
                db,
                "application_history",
                '''
                CREATE TABLE application_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER, -- NULL 許可
                    applicant           TEXT NOT NULL,
                    application_content TEXT,
                    applicant_comment   TEXT,
                    application_datetime TEXT NOT NULL,
                    approver            TEXT,
                    approver_comment    TEXT,
                    approval_datetime   TEXT,
                    status              TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES item(id) ON DELETE SET NULL
                )
                ''',
                copy_cols=["id","item_id","applicant","application_content","applicant_comment",
                           "application_datetime","approver","approver_comment","approval_datetime","status"],
                post_sql=[
                    "CREATE INDEX IF NOT EXISTS idx_app_hist_item ON application_history(item_id)"
                ]
            )

        # 3) 孤児掃除
        db.execute("DELETE FROM child_item        WHERE item_id NOT IN (SELECT id FROM item)")
        db.execute("DELETE FROM checkout_history  WHERE item_id NOT IN (SELECT id FROM item)")
        db.execute("DELETE FROM inventory_check   WHERE item_id NOT IN (SELECT id FROM item)")
        db.execute("UPDATE item_application   SET item_id=NULL WHERE item_id NOT IN (SELECT id FROM item)")
        db.execute("UPDATE application_history SET item_id=NULL WHERE item_id NOT IN (SELECT id FROM item)")

        # 4) バージョン更新（db_meta は既に存在するのでOK）
        db.execute("INSERT OR REPLACE INTO db_meta(key, value) VALUES('schema_version', ?)", (str(3),))
        db.commit()