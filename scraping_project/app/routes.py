from flask import render_template, request, redirect, url_for, session, flash, send_from_directory
from .contorollers.create_animan_script import main
from .contorollers.get_animan_url import main as get_animan_main
from .contorollers.get_onsei_index import main as get_onsei_index_main
from .contorollers import create_animan_video
from .contorollers import create_reaction_script
import os
import shutil
import tempfile
from app.models import Voice


def register_routes(app):
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/get_onsei_index', methods=['GET', 'POST'])
    def get_onsei_index():
        if request.method == 'POST':
            try:
                get_onsei_index_main()  # get_index.pyのmain関数を実行
                session['message'] = 'データ抽出が完了しました'
                return redirect(url_for('get_onsei_index'))
            except Exception as e:
                flash(f'エラーが発生しました: {str(e)}')
        voices = Voice.query.all()
        return render_template('get_onsei_index.html', voices=voices)

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
                title_voice = request.form.get('title_voice')
                narration_voices = request.form.getlist('narration_voices[]')
                main(url, title_voice, narration_voices)  # URLを渡してスクリプトを実行
                session['message'] = '台本作成が完了しました'
                return redirect(url_for('success'))
            except Exception as e:
                flash(f'エラーが発生しました: {str(e)}')
                return redirect(url_for('error'))
        voices = Voice.query.all()
        return render_template('create_animan_script.html', voices=voices)

    @app.route('/create_reaction_script', methods=['GET', 'POST'])
    def create_reaction_script_page():
        if request.method == 'POST':
            try:
                csv_name, warnings = create_reaction_script.main(
                    request.form.get('urls', '').split(),
                    request.form.getlist('saved_files'),
                    request.form.get('theme', ''),
                    request.form.get('copyright', ''),
                    request.form.get('hashtags', ''),
                    request.form.get('title_voice'),
                    request.form.getlist('narration_voices[]'),
                )
                for warning in warnings:
                    flash(warning)
                session['message'] = f'反応集の台本を作成しました: {csv_name}'
                return redirect(url_for('success'))
            except Exception as e:
                flash(f'エラーが発生しました: {str(e)}')
                return redirect(url_for('error'))
        voices = Voice.query.all()
        return render_template('create_reaction_script.html', voices=voices,
                               saved_threads=create_reaction_script.list_saved_threads())

    @app.route('/create_animan_video', methods=['GET', 'POST'])
    def create_animan_video_page():
        if request.method == 'POST':
            work_dir = tempfile.mkdtemp(prefix='animan_upload_')
            try:
                # 背景画像・BGMは一時フォルダに保存して渡す
                uploads = {}
                for key in ('background', 'bgm'):
                    file = request.files.get(key)
                    if file and file.filename:
                        path = os.path.join(work_dir, key + os.path.splitext(file.filename)[1])
                        file.save(path)
                        uploads[key] = path
                output_path = create_animan_video.main(
                    request.form['csv_name'],
                    background_path=uploads.get('background'),
                    bgm_path=uploads.get('bgm'),
                    voicevox_url=request.form.get('voicevox_url') or 'http://127.0.0.1:50021',
                    speed=float(request.form.get('speed') or 1.2),
                    silent=bool(request.form.get('silent')),
                )
                flash(f'動画作成が完了しました: {os.path.basename(output_path)}')
                return redirect(url_for('video_list'))
            except Exception as e:
                flash(f'エラーが発生しました: {str(e)}')
                return redirect(url_for('error'))
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
        return render_template('create_animan_video.html', scripts=create_animan_video.list_scripts())

    @app.route('/videos')
    def video_list():
        video_dir = os.path.abspath(create_animan_video.VIDEO_DIR)
        videos = []
        descriptions = []
        if os.path.isdir(video_dir):
            names = [f for f in os.listdir(video_dir) if f.endswith('.mp4')]
            videos = sorted(names, key=lambda f: os.path.getmtime(os.path.join(video_dir, f)), reverse=True)
            descriptions = [f for f in os.listdir(video_dir) if f.endswith('_説明文.txt')]
        return render_template('video_list.html', videos=videos, descriptions=descriptions)

    @app.route('/videos/<path:filename>')
    def video_file(filename):
        # send_from_directoryはディレクトリ外へのパス指定を弾く
        return send_from_directory(os.path.abspath(create_animan_video.VIDEO_DIR), filename)

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