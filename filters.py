# filters.py
import json

def loadjson_filter(s):
    """JSON文字列→Pythonオブジェクト。既にdict/listならそのまま返す。失敗時は{}。"""
    if s is None or s == "":
        return {}
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except Exception:
        return {}

def register_filters(app):
    app.jinja_env.filters['loadjson'] = loadjson_filter
