from flask import render_template, request, redirect, url_for, session, flash
from .create_animan_script import main
from .get_animan_url import main as get_animan_main


def register_routes(app):
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/get_onsei_index', methods=['GET', 'POST'])
    def get_onsei_index():
        return render_template('get_onsei_index.html')

    @app.route('/get_animan_url', methods=['GET', 'POST'])
    def get_animan_url():
        if request.method == 'POST':
            # フォームデータを取得
            excel_title = request.form['excel_title']
            sheet_title = request.form.get('sheet_title', '')
            stop_title = request.form.get('stop_title', '')
            base_url = request.form['base_url']
            start_url = request.form['start_url']
            # スクリプト実行
            try:
                get_animan_main(excel_title, sheet_title, stop_title, base_url, start_url)
                # メッセージをセッションに格納してリダイレクト
                session['message'] = 'URL抽出が完了しました'
                return redirect(url_for('success'))
            except Exception as e:
                flash(f'エラーが発生しました: {str(e)}')
                return redirect(url_for('error'))
        return render_template('get_animan_url.html')

    @app.route('/create_animan_script', methods=['GET', 'POST'])
    def create_animan_script():
        if request.method == 'POST':
            try:
                url = request.form['url']
                main(url)  # URLを渡してスクリプトを実行
                session['message'] = '台本作成が完了しました'
                return redirect(url_for('success'))
            except Exception as e:
                flash(f'エラーが発生しました: {str(e)}')
                return redirect(url_for('error'))
        return render_template('create_animan_script.html')

    @app.route('/success')
    def success():
        message = session.pop('message', None)
        return render_template('success.html', message=message)

    @app.route('/error')
    def error():
        return render_template('error.html')

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404


    return app