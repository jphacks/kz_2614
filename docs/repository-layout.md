# フォルダー構成と担当領域

```text
kz_2614/
├── README.md                 # JPHACKS公開用の製品・技術・担当説明
├── apps/
│   ├── web/                  # HOSSy: ブラウザー画面と音声入出力
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
├── scripts/                  # ローカル起動・MCP設定・音声準備
├── tests/
│   ├── test_*.py             # サーバー・AI境界・HTTP/TCP
│   └── web/                  # Web/AudioWorkletとブラウザー模擬試験
└── docs/
    ├── api/                  # 共有する通信仕様
    ├── setup/                # 環境構築とこのPCの記録
    ├── requirements/         # 要件・受け入れ条件
    ├── development/          # 分担・ブランチ・検証・作業履歴
    ├── esp32/                # 機器の引き継ぎと当時の記録
    ├── archive/              # 旧Pi案・旧構成案
    └── licenses/             # 同梱ライセンス原文
```

`apps/hossy-web/`は別プロジェクトから追加した製品UI・PWAです。[起動と構成](../apps/hossy-web/README.md)を参照してください。元の`astro-site/`とComposeを保持し、既存`apps/web/`のサーバー配信先は変更していません。

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

Pythonは `server.api` / `server.ai` に統一。Webファイルはserver/api/server.pyがルートのapps/webから配信します。Arduinoはスケッチ名とフォルダー名の一致を保ちます。Androidはapps/androidをAndroid Studioで開きます。

個別環境の移行は [移行手順](development/layout-migration.md) を参照してください。Gitに入っていない設定・モデルはソース移動だけでは移りません。
