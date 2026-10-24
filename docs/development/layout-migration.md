# 旧Programs配置からの移行

ソースは [配置表](../repository-layout.md) のとおり移動しました。Gitに含めないファイルはpullしても自動で移動しません。更新前に会話を停止し、個別データをバックアップしてから次を対応してください。

| 旧ローカル設定 | 新しい保存先 |
| --- | --- |
| Programs/ServerSide/.env | server/.env |
| Programs/ServerSide/dashboard/data | server/api/data |
| Programs/ServerSide/whisper | server/whisper |
| Programs/ServerSide/models | server/models |
| Programs/ServerSide/STTLLMTTS/third_party/Irodori-TTS | server/ai/third_party/Irodori-TTS |
| Programs/ClientSide/androidVer/local.properties | apps/android/local.properties |
| Programs/ClientSide/ESP32/ESP32-Reader/WiFiConfig.h | firmware/esp32/ESP32-Reader/WiFiConfig.h |
| Programs/ClientSide/ESP32/ESP32-Follower/WiFiConfig.h / TailConfig.h | firmware/esp32/ESP32-Followerの同名ファイル |

既存の移行先を上書きせず、内容を比較してからコピーしてください。モデルは再ダウンロード不要です。`.env`に指定した絶対パスは、その参照先を動かさない限り維持できます。新配置に移す場合は、対応するパス設定も変更します。

仮想環境は `server/.venv` が新しい入口です。既存venvは内部の実行スクリプトに絶対パスを含む場合があるので、ディレクトリを単純移動しません。このPCでは元から外部venvへのリンクなので同じ参照先のリンクを作ります。他の環境では新しいvenvをセットアップするか、元venvへのリンクを用意してください。Windowsのリンクには環境に応じた権限が必要です。

更新後はルートの `./service-local.sh start`、または `bash server/start_server_ubuntu.sh` / `server/start_server_windows.ps1` で起動します。手動起動のPythonモジュール名は `server.api.server` と `server.ai.voice.tcp_server` です。

WebのURLは変わりません。再起動後の会話は新規に開始してください。サーバーの設定・参照音声の表示と、音声サービスの応答を確認してから旧保存先の整理に進みます。旧ProgramsディレクトリはGit対象外にしており、移行前データが残っていても公開されません。
