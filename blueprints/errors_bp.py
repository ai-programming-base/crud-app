# blueprints/errors_bp.py
from flask import Blueprint, render_template, request
from services import logger  # "myapp" の共通ロガー

errors_bp = Blueprint("errors_bp", __name__)

@errors_bp.app_errorhandler(404)
def handle_404(e):
    logger.info("404 Not Found: %s", request.path)
    return render_template("errors/404.html", path=request.path), 404

@errors_bp.app_errorhandler(500)
def handle_500(e):
    # 例外スタックを含めて出力
    logger.exception("500 Internal Server Error at %s", request.path)
    return render_template("errors/500.html"), 500
