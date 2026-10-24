# ESP32 Reader / Follower とSG90 3台

Readerが可変抵抗器を読み、Wi-Fi経由でFollowerのSG90 3台へ共通の指令を送る。しっぽの先端指令は0～180で、0が丸い側、90が中立、180が伸びる側。これは論理指令であり、実測した先端角度ではない。

詳細な現行仕様は [docs/esp32](../../docs/esp32/README.md) にまとめた。

| フォルダ | 内容 |
| --- | --- |
| [ESP32-Reader](ESP32-Reader/ESP32-Reader.ino) | 可変抵抗器の入力、ADC校正、HTTP送信 |
| [ESP32-Follower](ESP32-Follower/ESP32-Follower.ino) | SG90 3台、HTTP API、有限タップ・無期限往復・中立・脱力 |
| [voice_tcp](voice_tcp/README.md) | 独立したESP-IDF音声通信部品。今回のサーボスケッチには組み込んでいない |

## Arduino IDEで使う

1. ボードを従来の`ESP32 Dev Module`に合わせる。既存の確認環境はArduino-ESP32 3.3.12。追加サーボライブラリは不要。
2. [Readerのテンプレート](ESP32-Reader/WiFiConfig.example.h)を`ESP32-Reader/WiFiConfig.h`、[Followerのテンプレート](ESP32-Follower/WiFiConfig.example.h)を`ESP32-Follower/WiFiConfig.h`へコピーし、同じ2.4 GHz Wi-Fiを設定する。
3. [TailConfig.example.h](ESP32-Follower/TailConfig.example.h)を`ESP32-Follower/TailConfig.h`へコピーする。配布テンプレートはタップ無効・90～90の未設定範囲なので、[実機設定と校正の仕様](../../docs/requirements/esp32.md)を確認する。
4. Followerを書き込み、115200 bpsのシリアルで役割、パルス設定、`Servo ESP32 IP:`を確認する。
5. Readerを書き込む。`RECEIVER_IP`が空なら`esp32-servo.local`を探索し、設定されていればそのIPを使う。
6. Followerは初期1500 µsの後、Readerのつまみへ追従する。保持したい場合はFollowerへ`n`、テレオペ再開は`t`、PWM停止は`d`を送る。

## 配線の要約

| 対象 | 接続 |
| --- | --- |
| 可変抵抗器 | 両端をReaderの3V3/GND、中央をGPIO34 |
| サーボ信号 | FollowerのGPIO18・19・21 |
| サーボ電源 | 3台分を供給できる外部5 V電源、ESP32と共通GND |
| 識別LED | GPIO2。Readerは1回、Followerは2回の点滅 |

可変抵抗器の入力へ5 Vを接続しない。サーボの電源はGPIO/3V3から取らず、USB給電中のESP32の電源端子へ外部電源の＋をつなぎ合わせない。

## APIとMCP

- [HTTP APIとcurl/PowerShell例](../../docs/api/esp32-http.md)
- [モード・校正・タイミング](../../docs/requirements/esp32.md)
- [現在のPC側MCPと実機向け提案の違い](../../docs/esp32/mcp-integration.md)
- [過去の実機記録](../../docs/esp32/history/2026-10-25-0222-LLMしっぽMCP統合調査.md)

APIのトークン設定は不要。Wi-Fiの実設定、`TailConfig.h`、ビルド生成物はGitに含めない。COM番号とIPは接続環境で変わる。

現在の会話サーバーはPCの希望頻度の保存・取得だけを公開する。履歴内のMCP提案Pythonを丸ごと置き換えても、現在の会話側ブリッジへは接続できない。詳細は上記MCP仕様を参照する。
