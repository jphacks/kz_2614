# ねんぎつね（仮）

**声で会話し、しっぽのトントンで寄り添う、きつね型の添い寝ロボット。**

JPHACKS 2026 / チーム `kz_2614`

## 製品概要

### 背景・解決したい課題

寝る前に少し話したい、ひとりの時間に誰かの気配がほしい。そのような場面に向け、短い音声会話と柔らかいしっぽの動きを組み合わせる添い寝クッションを開発しています。

### 製品説明

マイクへ話しかけるとPC上で音声を認識し、返答を生成して端末へ音声を返します。並行して開発したESP32 Reader・Followerは、可変抵抗器の入力やHTTP要求に応じてSG90サーボ3台を制御します。会話からの機器制御は統合途中です。

### 特長・注力したこと

1. **話している途中から文字が更新される会話。** 音声を継続送信し、発話検出と文字起こしをPC側へ集約しています。
2. **話しかけ直せる音声応答。** 返答の生成・出力中も入力を受け付け、新しい発話が始まると前の返答を打ち切ります。
3. **音声と機器の役割分担。** Web・Android・ESP32の入出力と、PCのAI処理を分けています。機器側には動作時間・速度・停止の制約を設けています。

## 現在のデモと確認状況

製品Webの公開先は [ねんぎつね 製品Web](https://hiroron-jphacks2026.hiroron.net/)、ダイアリー入口は [#diary](https://hiroron-jphacks2026.hiroron.net/#diary) です。2026-10-25の未ログイン確認ではhiroron Passportのログイン画面で保護されており、ログイン後の製品UI・音声接続は未確認です。PCローカル8765番の音声・設定Webとは別の入口です。[公開環境の結合テスト表](docs/demo.md#公開製品webの結合テスト準備) に確認順序をまとめています。

| 機能 | 状態 |
| --- | --- |
| HOSSyの製品Web・PWA | 最新UIを保持し、同一originの音声APIへ接続するコードを実装。実マイク・公開環境・実機は未検証 |
| PCのWeb画面からSTT → LLM → TTSの音声往復 | 実モデルで確認済み |
| 連続PCM入力、返答PCM配信、割込み、短い通信遅延からの復旧 | 実装済み。自動テストと記録済みのHTTP試験あり |
| ESP32 Reader/Follower・SG90制御 | ファームウェアとEMORI側の実機記録あり |
| 会話のMCPからESP32を直接動作 | 未完了。現行MCPはPC内の希望頻度の保存・取得 |
| 3Dプリント部品 | STL 8点を収録。寸法・材料・印刷条件・実機との嵌合は未確認 |
| Android / ESP32の新しい音声TCP経路 | コードあり。最新版の端末ビルド・実機通話は未確認 |

公開用デモ動画・製品写真はまだ掲載していません。[デモ手順と確認範囲](docs/demo.md) に、再現方法と残っている確認を記載しています。

## 技術構成

```text
製品Web / PWA ─ 同一originの音声APIへ（配信サーバーの転送設定が必要）
              └ 端末内の選択・開始/終了時刻・終了理由

音声管理Webのマイク ─HTTP PCM→ PCサーバー ─PCMフレーム→ Webスピーカー
                         │
                  Silero VAD → Whisper → Gemma → Irodori-TTS
                         │
                  MCP（現在は頻度の保存・取得）

ESP32 Reader ─Wi-Fi→ ESP32 Follower → SG90 × 3
                     ↑
              PC会話との実接続は今後の統合
```

| 領域 | 使用技術 | ソース |
| --- | --- | --- |
| AI・会話制御 | Python、faster-whisper、Silero VAD、LM Studio、Irodori-TTS、MCP | [server/ai](server/ai/) |
| HTTP・設定・音声API | Python HTTPサーバー、JSON、NDJSON、PCM16LE | [server/api](server/api/) |
| 製品Web / PWA | Astro、JavaScript、localStorage、Service Worker | [apps/hossy-web](apps/hossy-web/) |
| 音声・設定Web | HTML、CSS、JavaScript、Web Audio / AudioWorklet | [apps/web](apps/web/) |
| Android | Java、Android SDK、Gradle、TCPソケット | [apps/android](apps/android/) |
| しっぽ・機器 | ESP32、Arduino C++、Wi-Fi/HTTP、SG90 × 3 | [firmware/esp32](firmware/esp32/) |
| 筐体・取付部品 | 3Dプリント用STL 8点 | [3Dprint](3Dprint/README.md) |
| ESP32音声通信部品 | C、ESP-IDF、TCP、PCM16LE | [firmware/esp32/voice_tcp](firmware/esp32/voice_tcp/) |

このPCで使用するモデルは `Systran/faster-whisper-small`、`google/gemma-4-26b-a4b-qat`、`Aratako/Irodori-TTS-v4.1-Small`。モデルの重みや外部ランタイムは配布元から別途取得します。

### チームで実装した部分

既存の音声認識・言語・音声合成モデルを接続する会話パイプライン、連続PCMの送受信、発話による返答中断、受理番号による送信復旧、参照音声処理の再利用、設定画面、MCP頻度操作、ESP32の入力・連動制御とHTTP APIを実装しています。モデルそのものを独自開発したものではありません。

## 作業分担

| 担当 | 主な領域 | 変更する場所 |
| --- | --- | --- |
| **kameyama** | AI・音声パイプライン、サーバー側の会話・MCP連携 | `server/ai/`、`server/api/`、`docs/api/` |
| **HOSSy** | 製品Web・PWA、音声画面・操作UI | `apps/hossy-web/`、`apps/web/`、`tests/web/` |
| **EMORI** | ESP32 Reader/Follower、しっぽ制御・機器側API | `firmware/esp32/`、機器仕様・API、`docs/esp32/` |
| 担当者名未指定（`namikawa_3Dprint`由来） | 3Dプリント用の筐体・取付部品 | [3Dprint/](3Dprint/README.md) |
| 担当者名未指定 | Android音声クライアント | `apps/android/` |

APIや音声形式の変更は双方の担当で確認します。詳細は [担当範囲と連携ルール](docs/development/ownership.md) を参照してください。

## 起動・開発

- [統合仕様書](docs/requirements/system.md) / [仕様書・処理フロー図の一覧](docs/README.md)

- [製品Webの使い方](docs/web/usage.md)（タイマー・日記・保存・PWA）
- [HOSSyのAstro製品UI・PWAの起動](apps/hossy-web/README.md)（音声API接続コードあり。実音声・公開経路は未検証）
- [Git管理・除外方針](docs/development/gitignore.md)

- [Windows・Ubuntuのセットアップ](docs/setup/server.md)
- [このPCの環境と起動](docs/setup/local-pc.md)
- [ESP32の配線・設定・起動](firmware/esp32/README.md)
- [構成と通信経路](docs/architecture.md)
- [3Dプリント部品一覧と確認事項](3Dprint/README.md)
- [公開ブランチの統合対象・状態](docs/development/branches.md)
- [フォルダー一覧](docs/repository-layout.md)
- [開発参加・テスト](CONTRIBUTING.md)

依存関係とモデルを用意したこのPCでは、ルートの `./service-local.sh start` でWebサーバーを起動し、`http://127.0.0.1:8765/` を開きます。クリーンなcloneでは先にセットアップが必要です。

HOSSyの製品Webは `cd apps/hossy-web && npm ci && npm run dev` で起動し、`http://127.0.0.1:4321/` を開きます。[画面仕様](docs/requirements/web-product.md) / [起動とPWA](docs/setup/hossy-web.md)。

## 今後の展望

製品Webの音声HTTP API接続の実環境検証、会話MCPとESP32実機の接続、Android/ESP32の音声入出力と停止の実機検証、利用環境に応じた応答品質・速度の調整を進めます。実装済み・実測済み・未確認を分けて [開発履歴](docs/development/work-history.md) に残します。

## ライセンス・公開データ

プロジェクトのコードは [MIT](LICENSE)。心音・Android robot・Gradle Wrapperは [第三者素材の表示](THIRD_PARTY_NOTICES.md) に従います。モデル、録音、参照音声、生成音声、実Wi-Fi設定、`.env`、ビルド成果物はリポジトリに含めません。
