# ClientSide

MVPはAndroidスマホのWeb画面で録音・音声送信・相づち／本返答の再生・設定・開始／停止を扱います。

必須動作、未決事項、受け入れ条件は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。スマホ向けアプリと接続するサーバーAPIはまだ未実装です。

## ESP32によるしっぽ制御

ESP32 Reader・FollowerのArduinoスケッチと設定テンプレート、SG90 3台の配線・API仕様・実機記録を [ESP32](ESP32/README.md) に追加しました。LLMへ接続するMCP提案Pythonとサーバー側Codexへの引き継ぎメモも同フォルダ内にあります。サーバー側の既存実装への反映は別途行います。
