# auth.py
# -*- coding: utf-8 -*-

"""
ユーザー認証処理を切り替えるための雛形
authenticate(username, password) を実装すること

【結果】
    認証OK: {'result': True, 'email': ..., 'department': ..., 'realname': ...}
    認証NG: {'result': False, 'reason': '...'}
"""

# --- SQLite認証（デフォルト） ---
def authenticate(username, password):
    import sqlite3
    from werkzeug.security import check_password_hash

    DB_PATH = 'items.db'
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user['password'], password):
            return {
                'result': True,
                'email': user['email'],
                'department': user['department'],
                'realname': user['realname'],
            }
        else:
            return {'result': False, 'reason': 'ユーザー名またはパスワードが違います'}
    except Exception as e:
        return {'result': False, 'reason': f'DBエラー: {e}'}


# --- LDAP認証サンプル（差し替え用） ---
# def authenticate(username, password):
#     from ldap3 import Server, Connection, ALL
#     LDAP_SERVER = "ldap://your-ldap-server"
#     LDAP_BASE_DN = "ou=users,dc=example,dc=com"
#
#     try:
#         # サーバー接続
#         server = Server(LDAP_SERVER, get_info=ALL)
#         conn = Connection(server, user=f'uid={username},{LDAP_BASE_DN}', password=password, auto_bind=True)
#         if conn.bind():
#             # 必要に応じて属性取得
#             conn.search(LDAP_BASE_DN, f'(uid={username})', attributes=['mail', 'departmentNumber', 'cn'])
#             entry = conn.entries[0]
#             return {
#                 'result': True,
#                 'email': str(entry.mail),
#                 'department': str(entry.departmentNumber),
#                 'realname': str(entry.cn),
#             }
#         else:
#             return {'result': False, 'reason': 'LDAP認証失敗'}
#     except Exception as e:
#         return {'result': False, 'reason': f'LDAPエラー: {e}'}

