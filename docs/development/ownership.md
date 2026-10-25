# 担当範囲と連携ルール

今回の範囲・境界・修理順・合格条件は [MVP.md](../../MVP.md) を正本とします。利用者指定は下表のGitHub名を優先し、以前の呼称との本人一致は断定しません。CODEOWNERSによる自動割当は設定していません。

| 担当 | 主な責務 | 主なソース | 対応する検証 |
| --- | --- | --- | --- |
| RyuuzinnRaika | STT・LLM・TTS、VAD、会話状態、MCP、HTTP/TCP受付 | server/ai、server/api | tests/test_*.py |
| naminamina | Web UI、設定操作、マイク送信、音声出力、独立した製品UI | apps/web、apps/hossy-web | tests/web、test_dashboard*.py、Astroビルド |
| Happy-Emori | Reader/Follower、SG90、機器側HTTP仕様・実機 | firmware/esp32、docs/esp32 | Arduinoでのビルド、機器の停止・動作確認 |
| 未指定（`namikawa_3Dprint`由来） | 筐体・サーボ等の取付部品 | [3Dprint](../../3Dprint/README.md) | 寸法・材料・印刷条件・嵌合の実機確認は未実施 |
| 未指定 | Androidの録音・出力・TCP接続 | apps/android | Gradleビルド、Android実機 |
| AI・機器担当の共同 | ESP32音声TCP部品の機器IOへの組込み | firmware/esp32/voice_tcp、server/ai/voice | PC TCP試験とESP-IDF・端末実機 |

takemi853の担当は未指定です。Androidや筐体の担当へ推測で固定しません。

3Dプリント部品はブランチ名を取り込み元として記録し、担当者の氏名・アカウントとの対応は推測しません。

## 変更をまたぐ場所

- 音声形式・イベント・停止: [HTTP仕様](../api/voice-http.md) / [TCP仕様](../api/voice-tcp.md) を先に揃え、AIとWeb/端末の双方を確認する。
- 部品形状・取付: [STL一覧](../../3Dprint/README.md) を確認し、ESP32・SG90・電池などの実物寸法や配線の逃げ、可動域を機器担当と確認する。ファイル名だけで印刷可能・組立済みとは判断しない。
- トントン: [PC内の頻度API](../api/tail-frequency.md) と [ESP32実機API](../api/esp32-http.md) は別契約。保存成功を機器動作成功と扱わない。
- 入力音量と発話検出: naminaminaが操作UI、RyuuzinnRaikaがサーバー適用・VADを担当。ブラウザー独自のVADを追加しない。
- ライセンス・動画・写真: 公開する担当が出典・許諾を確認し、THIRD_PARTY_NOTICES.mdへ記録する。個別の声や実設定は公開しない。

## 作業単位

利用者指定の[contributors名の4人分の作業ブランチ](branches.md)を使います。個別機能をさらに分ける場合はmainから `feature/voice-...`、`feature/web-...`、`feature/android-...`、`feature/esp32-...` または `fix/...` を作り、小さい変更を統合します。担当者だけが変更できるという制限ではありません。境界の変更は関係担当に確認できる説明とテストを残します。


## 仕様書の共同管理

今回の境界は [MVP.md](../../MVP.md)、広い元計画は [統合仕様書](../requirements/system.md)、通信契約は [API一覧](../api/README.md) を参照します。EMORIの機器仕様は [requirements/esp32.md](../requirements/esp32.md)、機器HTTPは [api/esp32-http.md](../api/esp32-http.md)。文書を置くフォルダーの分類と担当者の責務は別であり、API側へ移しても機器仕様の担当は変わりません。
