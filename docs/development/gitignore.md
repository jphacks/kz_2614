# Gitで管理するもの・除外するもの

2026-10-25更新。仕様整理を引き継ぎ、配置はapps/server/firmware/docsに対応します。共通規則をルートに、固有の保存場所・生成物を各コンポーネントにまとめています。

| 対象 | 方針 | 規則の場所 |
| --- | --- | --- |
| ソース、テスト、仕様、ライセンス | 管理する | リポジトリ全体 |
| `.env.example`、WiFi/Tailの `*.example.h`、`mcp.json.example` | 管理する。実際の接続情報を入れない | ルートの例外とファイル名 |
| `.env` と派生設定、実Wi-Fi/Tail設定、`local.properties` | 除外する | ルート `.gitignore` |
| 録音・参照・生成音声、モデル、APK等 | 除外する | ルート `.gitignore` |
| `server/api/data/` の設定JSON、参照WAV、相づちmanifest等 | 拡張子によらず除外する。`.gitkeep`だけ許可 | server/.gitignore |
| Whisper・モデル・外部ランタイム | 本体を除外し、READMEやplaceholderを保持 | server/.gitignore |
| Arduino CLI・キャッシュ・調査コピー・ビルド | 除外する | ESP32 `.gitignore` |
| ESP-IDF build、実sdkconfig、取得コンポーネント、生成lock | 現行方針を維持して除外する | firmware/esp32/voice_tcp/.gitignore |
| Gradle・CMake生成物、Android Studio個人設定 | 除外する | apps/android/.gitignore |
| Original付きローカル保管、ログ、Python/Nodeキャッシュ | 除外する | ルート `.gitignore` |
| 配布する心音 `heartbeat.ogg` | 個別例外として管理する | ルート `.gitignore`。条件は[第三者素材](../../THIRD_PARTY_NOTICES.md) |

`/whisper` のように親ディレクトリ全体を除外すると、その中のREADMEを `!` で戻せません。サーバーではまず `/whisper` を除外し、実ディレクトリだけ `!/whisper/` で戻したうえで `/whisper/*` により内容を除外し、READMEと `.gitkeep` を明示許可します。既存PCのWhisperキャッシュへのシンボリックリンクは除外したままです。`.venv` も実ディレクトリ・シンボリックリンクの両方を除外します。

音声などの拡張子規則はルート以下に共通で適用します。テスト用音声を追加する場合も、権利・内容を確認して具体的なファイルを例外指定し、広い除外解除は行いません。既存のGradle Wrapper JARや設定テンプレートは引き続き管理できます。

## 変更時の確認

次のコマンドはファイルを削除・変更しません。`check-ignore -v` は適用規則も表示します。`!` で始まる規則は管理を許可する例外です。

```bash
git check-ignore -v --no-index server/.env
git check-ignore -v --no-index server/.env.example
git check-ignore -v --no-index server/api/data/reference_voice.wav
git check-ignore -v --no-index firmware/esp32/ESP32-Follower/WiFiConfig.h
git status --short --untracked-files=all
git diff --check
```

`.gitignore` は既に追跡されたファイルを履歴から消す機能ではありません。強制追加を防ぐアクセス制御でもありません。変更しただけでローカルの設定・音声・モデルが削除されることはありません。追跡ファイルの除外や履歴からの削除は別の操作なので、今回の整理では行いません。

変更前のファイルは作業フォルダー外の `Original-specs-975b3ad-2026-10-25/` に `.Original` 付きで保存し、SHA-256を検証しています。このPCの保管物であり、Gitへは追加しません。
