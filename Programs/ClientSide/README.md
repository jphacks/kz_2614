# クライアントと機器

音声認識・発話検出・返答生成はPCサーバーが担当します。Web画面は [ServerSide/dashboard/web](../ServerSide/dashboard/web/) にあります。

| フォルダー | 役割 | 確認状況 |
| --- | --- | --- |
| [androidVer](androidVer/) | Androidネイティブ音声アプリ。PCへ直接TCP接続 | 最新方式のAPKビルド・実機通話は未確認 |
| [ESP32/VoiceTCP](ESP32/VoiceTCP/README.md) | ESP32用の音声TCP通信部品 | マイク・スピーカー・GPIO制御を含まない。ビルド・実機は未確認 |
| [ESP32](ESP32/README.md) | Reader/FollowerのArduinoスケッチ、SG90 3台の制御、HTTP API | EMORI側の実機記録あり。このPCの会話MCPとの実接続は未完了 |
| [旧Pi構成案](../../docs/archive/raspberry-pi/RASPBERRY_PI_API.md) | 以前のRaspberry Pi構成案 | 現行ESP32構成では使用しない |

音声通信は [VOICE_TCP.md](../ServerSide/VOICE_TCP.md)、要件は [REQUIREMENTS.md](REQUIREMENTS.md) を参照してください。ESP32のMCP提案は引き継ぎ資料であり、稼働中サーバーのMCPを置換したものではありません。
