あにまんというサイトの過去ログ（https://bbs.animanch.com/kakolog/） の情報をまとめるためのもの  
1.URL抽出ページ  
各スレッドのタイトル、コメント数、URLを抽出し、エクセル出力するもの  
2.台本作成ページ  
スレッドURLを入力するとYMM4に自動読み込みが出来るcsvファイルを作成する。  
3.動画作成ページ  
台本ファイル（csv）からVOICEVOXで音声を合成し、反応集動画（mp4）を作成する。  
出力先：`scraping_project/出力動画/`（動画・字幕srt・サムネイル画像）  

### 動画作成の準備
- [VOICEVOX](https://voicevox.hiroshiba.jp/) を起動しておく（エンジンが http://127.0.0.1:50021 で待ち受ける）
- [ffmpeg](https://ffmpeg.org/) をインストールしてPATHを通す（または環境変数 `FFMPEG_PATH` にffmpeg.exeのパスを設定）
- `pip install -r requirements.txt`

### 使い方
1. 台本作成ページでスレッドURLから台本csvを作成（必要ならExcel等で不要なレスを削除・編集）
2. 動画作成ページで台本を選び、背景画像・BGMを指定して作成  
   コマンドラインからも作成可能：`python make_video.py 台本名.csv --background 背景.png --bgm bgm.mp3`  
   `--silent` を付けるとVOICEVOXなしでレイアウト確認用の動画を作成する

### スマホからこのPCで実行する
1. PCで `scraping_project/start_for_smartphone.bat` を実行する（PowerShell等なら `APP_HOST=0.0.0.0` を環境変数に設定して `python run.py`）
2. 画面に表示された `http://192.168.x.x:5000` をスマホのブラウザで開く（PCとスマホは同じWi-Fi）
3. 開けない場合はWindowsファイアウォールでPython(ポート5000)の受信を許可する

- 作成処理は**PC上で**実行されます。VOICEVOX・ffmpegもPC側で起動・用意しておいてください（VOICEVOXエンジンURLは `http://127.0.0.1:50021` のままでOK）
- 外出先から使う場合は [Tailscale](https://tailscale.com/) をPCとスマホに入れ、表示されるTailscaleのIPで `http://<TailscaleのIP>:5000` を開く（ルーターのポート開放はしないこと。認証がないため）
- 外部公開モードではFlaskのデバッグモードは自動的にOFFになる。ポート変更は `APP_PORT` で指定

