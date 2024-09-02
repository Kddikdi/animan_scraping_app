from app import create_app, db

# Flaskアプリケーションのインスタンスを作成
app = create_app()

# アプリケーションコンテキストを設定
with app.app_context():
    # データベースのテーブルを作成
    db.create_all()
