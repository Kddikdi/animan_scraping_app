import os
import socket

from app import create_app

app = create_app()


def get_lan_ip():
    """同じWi-Fi内のスマホからアクセスするためのPCのIPアドレスを取得する"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))  # 実際には通信しない（経路選択のみ）
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


if __name__ == "__main__":
    # APP_HOST=0.0.0.0 でスマホ等の他端末からアクセス可能になる（既定はPC内のみ）
    host = os.environ.get('APP_HOST', '127.0.0.1')
    port = int(os.environ.get('APP_PORT', '5000'))
    exposed = host not in ('127.0.0.1', 'localhost')
    if exposed:
        print(f'スマホのブラウザから http://{get_lan_ip()}:{port} を開いてください')
    # 外部公開時はデバッガ(任意コード実行が可能)を無効にする
    app.run(host=host, port=port, debug=not exposed)
