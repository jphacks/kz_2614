# 現在の構成（2026-10-25）

## 音声

```text
Webマイク → HTTP PCM入力 → PCのSilero VAD → Whisper small
                                             ↓
                                   Gemma 4 26B / LM Studio
                                             ↓
Webスピーカー ← NDJSONのPCMフレーム ← Irodori-TTS
```

音声は16kHz・mono・PCM16LE。Web入力は通常256ms分ずつ、出力は合成済みの文節を100ms以下に分けて送ります。TTSモデル内部で生成中のサンプルを取り出す方式ではありません。返答の生成・出力中も入力を受け、サーバーVADで新しい発話が始まると前の返答を打ち切ります。短い通信遅延では番号確認と再送で回復し、入力保存は約30秒までです。

このPCの設定はSTT `Systran/faster-whisper-small`、LLM `google/gemma-4-26b-a4b-qat`、TTS `Aratako/Irodori-TTS-v4.1-Small`。稼働モデルと次回設定はWebの稼働表示で区別します。モデル本体と個別の声の参照WAVはGitに含めません。

## しっぽ

```text
現在: 会話 → アプリ所有stdio MCP → 希望頻度の保存・取得（PC内）
機器: ESP32 Reader → Wi-Fi → ESP32 Follower → SG90×3
次の統合: PCのMCP → Follower HTTP API（未接続）
```

`emori-ESP32`のReader/FollowerソースとAPI仕様を統合済み。EMORI側の実機確認記録は保存していますが、このPCからの実機試験はしていません。MCP提案はdocs/esp32/historyに置き、実行用のtail_mcp_server.pyと分離します。単純置換では会話側の2ツール用検証と一致しないため、実動作の接続は別途実装が必要です。

## 接続先と検証範囲

| 経路 | ポート・場所 | 状態 |
| --- | --- | --- |
| Web | HTTP 8765 | PCで起動・実モデル往復を確認済み |
| LLM | HTTP 1234 | LM Studio。音声サーバーとは別プロセス |
| Android直接音声 | TCP 8766 | コードあり。最新版のAPK・実機通話は未確認 |
| ESP32音声 | TCP 8766、ClientSide/ESP32/VoiceTCP | 通信部品のみ。ビルド・実機未確認 |
| ESP32しっぽ | Follower HTTP API | 機器側ソース・実機記録あり。PC会話との連携は未完了 |

WebのサーバーVAD・連続PCM・割込みに関するテスト成功は、Android/ESP32端末でも同じ挙動が確認済みという意味ではありません。特に100msの出力profileと割込み通知への端末側対応には確認が残ります。

## フォルダー移行

| 以前 | 現在 |
| --- | --- |
| Programs/ClientSide/ESP32Voice | Programs/ClientSide/ESP32/VoiceTCP |
| Programs/ClientSide/ESP32/codex-memo | docs/esp32/history |
| Programs/ClientSide/Raspi4Ver | docs/archive/raspberry-pi |

ServerSide、androidVer、ルートの起動スクリプトは既存パスを維持します。外部モデル・仮想環境へのローカル参照も維持するため、このPCの旧kz_2614フォルダーは削除しません。
