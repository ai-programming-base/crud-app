# send_mail.py

def send_mail(to, subject, body):
    """
    メール送信のダミー実装。
    Args:
        to (str|list): 宛先メールアドレス（複数対応も可）
        subject (str): 件名
        body (str): 本文
    Returns:
        dict: {'success': True/False, 'error': None or '詳細メッセージ'}
    """
    try:
        # ここで実際のメール送信処理を実装する（ダミー）
        print(f"[MAIL] To:{to} / Subject:{subject} / Body:{body}")
        # 実際にはsmtplibやsendgrid等で送信
        return {'success': True, 'error': None}
    except Exception as e:
        return {'success': False, 'error': str(e)}
