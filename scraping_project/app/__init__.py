from flask import Flask


def create_app():
    app = Flask(__name__)
    app.secret_key = 'Kddikdi20'  # ここに秘密のキーを設定
    # ルートを登録するためにroutesをインポート
    with app.app_context():
        from .routes import register_routes
        register_routes(app)

    return app
