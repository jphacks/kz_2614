# 自動テスト

ルートの `test_*.py` はサーバー・AI接続境界・会話状態・HTTP/TCPを確認します。`web/` はNode.jsで動くブラウザー模擬試験・AudioWorklet試験です。Pythonのtest_dashboard.py等からWeb試験も呼び出します。

リポジトリルートで `server/.venv/bin/python -m unittest discover -s tests -v` を実行してください。Node.jsはPATHまたはNODE環境変数で指定します。

モデル推論を模擬するテストの成功と、実モデル・物理スピーカー・Android/ESP32実機での成功は分けて記録してください。今回の配置変更では、静的ファイルがapps/webから取得でき、MCP起動先がserver/aiを指すことも確認します。
