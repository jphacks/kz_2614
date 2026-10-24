# フォルダー構成と担当領域

```text
kz_2614/
├── README.md                 # JPHACKS公開用の製品・技術・担当説明
├── apps/
│   ├── web/                  # 音声・設定Web / Python配信 / 8765
│   ├── hossy-web/            # HOSSy: 独立したAstro製品UI・PWA
│   └── android/              # Androidネイティブ音声アプリ
├── server/                   # kameyama: PCサーバー
│   ├── api/                  # HTTP受付、設定、稼働情報
│   ├── ai/
│   │   ├── stt/              # 文字起こし
│   │   ├── llm/              # LM Studio・MCPツール橋渡し
│   │   ├── tts/              # 音声合成・参照キャッシュ
│   │   ├── voice/            # VAD・会話・割込み・PCM・TCP
│   │   ├── tail_mcp_server.py # 頻度MCP
│   │   └── third_party/      # 外部ランタイム配置先・セットアップ
│   ├── .env.example
│   └── requirements*.txt
├── firmware/esp32/           # EMORI: 機器側
│   ├── ESP32-Reader/         # 可変抵抗器の読取り
│   ├── ESP32-Follower/       # SG90・HTTP制御
│   └── voice_tcp/            # 音声通信部品。機器IOとの統合は別
├── 3Dprint/                  # namikawa_3Dprint由来: 筐体・取付部品のSTL 8点
├── scripts/                  # ローカル起動・MCP設定・音声準備
├── tests/
│   ├── test_*.py             # サーバー・AI境界・HTTP/TCP
│   └── web/                  # Web/AudioWorkletとブラウザー模擬試験
└── docs/
    ├── api/                  # 共有する通信仕様
    ├── setup/                # 環境構築とこのPCの記録
    ├── requirements/         # 統合仕様・サーバー・端末・ESP32
    ├── development/          # 分担・ブランチ・検証・作業履歴
    ├── web/                  # 製品Webの案内と元のメモ・画像
    ├── esp32/                # MCP統合案内・引き継ぎ・当時の記録
    ├── archive/              # 旧Pi案・旧構成案
    └── licenses/             # 同梱ライセンス原文
```

`apps/hossy-web/`は別プロジェクトから追加した製品UI・PWAです。[起動と構成](../apps/hossy-web/README.md)を参照してください。`package.json`・`src/`・`public/`・Dockerfile・Composeを直下へ集約しました。開発メモ・画像は`docs/web/history/`へ移動し、既存`apps/web/`のサーバー配信先は維持します。

[3Dprint/README.md](../3Dprint/README.md) に全8点の一覧と用途の確認範囲を記載しています。STLは取り込み元の配置・内容を保持し、寸法・材料・印刷条件・実機との嵌合は未確認です。

## 旧配置からの対応

| 旧配置 | 新配置 |
| --- | --- |
| Programs/ServerSide/dashboard/web | apps/web |
| Programs/ServerSide/dashboard | server/api |
| Programs/ServerSide/STTLLMTTS | server/ai |
| Programs/ServerSide | server（API仕様・手順はdocsへ） |
| Programs/ClientSide/androidVer | apps/android |
| Programs/ClientSide/ESP32 | firmware/esp32 |
| Programs/ClientSide/ESP32/VoiceTCP | firmware/esp32/voice_tcp |
| Programs/WORK_HISTORY.md | docs/development/work-history.md |
| LOCAL_SETUP.md | docs/setup/local-pc.md |
| apps/hossy-web/astro-site/* | apps/hossy-web/* |
| apps/hossy-web/Codex-memo | docs/web/history/hossy |
| apps/hossy-web/DEPLOY.md（旧本文） | docs/web/history/standalone-deploy.md |

Pythonは `server.api` / `server.ai` に統一。Webファイルはserver/api/server.pyがルートのapps/webから配信します。Arduinoはスケッチ名とフォルダー名の一致を保ちます。Androidはapps/androidをAndroid Studioで開きます。

個別環境の移行は [移行手順](development/layout-migration.md) を参照してください。Gitに入っていない設定・モデルはソース移動だけでは移りません。


## 仕様書の配置

```text
docs/
├── README.md                   # 全資料の入口
├── architecture.md             # 全体のMermaid処理図
├── requirements/
│   ├── README.md
│   ├── system.md               # 統合仕様書
│   ├── server.md               # PCサーバー
│   ├── clients.md              # 音声端末
│   ├── web-product.md          # 製品Webの画面・保存・未接続範囲
│   └── esp32.md                # Reader/Follower・サーボ
├── api/
│   ├── README.md
│   ├── voice-http.md
│   ├── voice-tcp.md
│   ├── tail-frequency.md       # PCの希望値保存
│   └── esp32-http.md           # Followerの実機HTTP
└── esp32/
    ├── README.md               # 機器担当向けの入口
    ├── mcp-integration.md      # 現行MCPと実機統合の条件
    ├── integration-review.md   # PC側の引き継ぎ調査
    ├── specification.md        # 移動先の案内のみ
    ├── http-api.md             # 移動先の案内のみ
    └── history/                # 過去の試験と提案原本
```

EMORIの仕様整理 `a786078` を取り込み、機器仕様と機器HTTPを共通分類へ移しました。旧パスは案内だけを残し、本文・図の二重管理を避けています。サーバー・機器の実行ソース、非公開設定・音声・モデルの配置は維持します。HOSSyのAstroソースだけ上表の新配置に整理しています。
