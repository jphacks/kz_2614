# API仕様一覧

HTTP音声、TCP音声、PCの保存設定、ESP32機器のAPIは別契約です。同じ「しっぽ」の設定でも接続先と成功の意味を区別してください。

今回の合格条件は [MVP.md](../../MVP.md)。PC/ESP32の修理目標は **[MVPしっぽ制御v2](mvp-tail-control-v2.md)（未実装）** に統一します。版確認、停止フェンス、遅着拒否とWebへの停止結果を定義し、現行v1とは区別します。担当はPC=RyuuzinnRaika、Web=naminamina、ESP32=Happy-Emoriです。下表の旧呼称は元資料の担当表記です。

| 仕様 | 接続する相手 | 成功が示す範囲 | 関係担当 |
| --- | --- | --- | --- |
| [HTTP音声・連続PCM](voice-http.md) | Web ↔ PC HTTPサーバー | 入力受理・イベント配信・出力通知 | kameyama / HOSSy |
| [直接TCP音声](voice-tcp.md) | Android/ESP32音声 ↔ PC TCPサーバー | 接続・入力受理・音声転送 | kameyama / 端末担当 |
| [PC内のトントン希望頻度](tail-frequency.md) | MCP ↔ PC設定API | 希望値の保存・取得。実機反映ではない | kameyama / HOSSy |
| [ESP32 Follower HTTP](esp32-http.md) | Reader/機器操作側 ↔ Follower | 機器の指令状態。物理位置の実測ではない | EMORI / kameyama |

会話MCPからFollowerへの実接続は未完了です。[MCP統合仕様](../esp32/mcp-integration.md) で現行2ツールと提案3ツールの差、独立停止、遅着拒否を確認してください。

関連: [統合仕様書](../requirements/system.md) / [機器仕様](../requirements/esp32.md) / [処理フロー](../architecture.md)。旧Pi APIはarchive内の過去案です。
