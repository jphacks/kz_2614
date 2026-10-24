# プログラムの配置

```text
Programs/
├── ServerSide/
│   ├── dashboard/             # Web画面・HTTP受付・設定保存
│   ├── STTLLMTTS/
│   │   ├── stt/               # Whisper文字起こし
│   │   ├── llm/               # LM Studioと会話用MCP橋渡し
│   │   ├── tts/               # Irodori音声合成
│   │   ├── voice/             # VAD・セッション・PCM・TCP
│   │   └── tail_mcp_server.py # 現行MCP（頻度設定・取得）
│   └── *.md                   # API仕様・セットアップ・要件
├── ClientSide/
│   ├── androidVer/            # Androidネイティブ音声アプリ
│   └── ESP32/
│       ├── ESP32-Reader/      # 可変抵抗器の入力
│       ├── ESP32-Follower/    # SG90 3台・HTTP制御
│       └── VoiceTCP/          # ESP32音声通信部品（機器IOなし）
└── WORK_HISTORY.md            # 双方の開発履歴
```

- [サーバー](ServerSide/README.md) / [端末・機器](ClientSide/README.md)
- [仕様書一覧](../docs/README.md) / [構成と処理フロー](../docs/architecture.md)
- [ESP32引き継ぎ資料](../docs/esp32/README.md)
- [旧構成案](../docs/archive/2026-10-24-programs-design.md) / [旧Pi API案](../docs/archive/raspberry-pi/RASPBERRY_PI_API.md)

Pythonのimport、起動スクリプト、AndroidのGradle設定との互換性を保つため、ServerSide/STTLLMTTSとandroidVerの名前は維持しています。旧ESP32VoiceはESP32/VoiceTCPへ、codex-memoはdocs/esp32/historyへ移動しました。
