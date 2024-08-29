from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# SQLAlchemyのインスタンスを作成
db = SQLAlchemy()


def create_app():
    app = Flask(__name__)
    app.secret_key = 'Kddikdi20'  # ここに秘密のキーを設定

    # データベースの設定
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///onsei_index.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # SQLAlchemyをアプリに関連付け
    db.init_app(app)

    # ルートを登録するためにroutesをインポート
    with app.app_context():
        from .routes import register_routes
        register_routes(app)

    return app
