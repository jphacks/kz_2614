# ESP32 音声TCPクライアント（通信部分のみ）

ユーザー指定により、GPIO、マイク、スピーカー、サーボ制御は実装しない。既存ESP32プログラムへ組み込むESP-IDF 5.3.2向けCコンポーネントを提供する。Androidアプリとは別のクライアント。ブラウザー・HTTP・WebSocketを介さずPCのTCPポートへ直接接続する。

## ファイル

- `components/voice_tcp/include/voice_tcp.h`: 公開API
- `components/voice_tcp/voice_tcp.c`: 接続、音声送受信、再生通知、切断
- `examples/transport_demo`: Wi-Fi接続後、10秒だけ無音PCMを送り切断する通信確認アプリ。GPIOも音声デバイスも使用しない

## 既存プロジェクトへの組み込み

`components/voice_tcp` を既存ESP-IDFプロジェクトの `components/` にコピーするか、トップレベルCMakeの `EXTRA_COMPONENT_DIRS` にこの `components` ディレクトリを指定する。呼び出し側のコンポーネントに `REQUIRES voice_tcp` を追加する。

Wi-Fi接続は既存側で完了させた後、次のように接続する。これは接続APIの例であり、マイク制御コードは含めない。

```c
#include "voice_tcp.h"

voice_tcp_client_t *client = NULL;
voice_tcp_config_t config = {
    .server_ipv4 = "192.168.137.201",
    .port = 8766,
};
esp_err_t result = voice_tcp_open(&config, &client);
// ESP_OKを確認後、送信タスクと受信タスクを開始する。
```

| API | 呼び出し側の責務 |
| --- | --- |
| `voice_tcp_open` | Wi-Fi接続後に1回呼ぶ。5秒のTCP接続期限と15秒の開始応答期限。接続ごとに新しいセッション |
| `voice_tcp_send_pcm` | PCM16LE・16kHz・monoの音声を継続して渡す。1〜32,000 bytesの偶数長。推奨512 samples＝1024 bytesを32ms間隔で送る。連番はライブラリで付与 |
| `voice_tcp_receive` | 独立した受信タスク1つから継続して呼ぶ。成功時は必ず `voice_tcp_event_free`。失敗時はイベント解放済みで接続を無効化する |
| `voice_tcp_playback` | 再生側が実際に再生を始めた時に `started`、complete受信後かつ全キュー再生終了時に `ended`、再生失敗時に `failed`。turn_idはイベントから取得 |
| `voice_tcp_request_stop` | 送受信をshutdownで中断する。サーバーは切断でセッションを廃棄する。ローカルの録音／再生停止は呼び出し側で行う |
| `voice_tcp_close` | 送信・受信・再生通知を呼ぶ全タスクの終了を確認してから呼ぶ。ハンドルを解放する |

送信タスクと受信タスクは別にする。送信と再生通知は内部mutexでフレーム単位に直列化する。複数受信タスク、closeとの並行呼び出しは不可。Wi-Fi切断・通信失敗時は自動で以前のセッションを復元しない。全タスク終了とcloseの後、新しくopenする。

## 受信イベントとメモリの所有権

`voice_tcp_event_t.json` はcJSONオブジェクト。`type` は `state`、`transcript`、`text_delta`、`acknowledgement`、`audio_chunk`、`complete`、`error`。state内にerrorがある場合も失敗として扱う。本文や認識テキストをログへ出さない。

`acknowledgement` と `audio_chunk` では `pcm` と `pcm_bytes` に復号済みのPCM16LE・16kHz・monoが入る。JSONのaudio_base64は復号後に削除してメモリを解放する。JSON、文字列、PCMポインターはすべて `voice_tcp_event_free` まで有効。再生タスクへ渡す場合は **イベント全体の所有権をキューへ移す** か必要なデータをコピーし、二重解放を避ける。受信タスクで全音声の再生を待つとACKも受信できなくなるため、再生側には上限付きキューを用意する。キュー満杯時はfailed通知と停止を行い、音声を黙って捨てない。

completeも音声と同じ順序のキューで扱う。complete受信だけでendedを送らない。相づちを含む最初の音声の実再生時にstartedを1回送り、途中の音声間の無音ではendedを送らない。

無音・返答生成中・返答再生中もPCM送信を続ける。VADと発話分割、生成／再生中の入力認識除外は既存サーバー側が担当する。ライブラリにはVADや再生判断を入れていない。

既定の受信パケット上限は1MiB、文節音声上限は512KiB（16.384秒）。文節を追加分割する機能は入れておらず、超過はサーバーが明示エラーにする。音声以外のパケットも含め上限を検証する。受信JSONバッファー、cJSON、復号PCMが同時に存在するため、実際の会話ではPSRAMと適切なmalloc設定を推奨。小容量端末はconfigで上限を小さくできるが、上限を超える返答は通話失敗になる。通常のESP32の内蔵RAMだけで全返答を再生できる保証はない。音声はファイルへ保存しない。

## 通信確認アプリ

ESP-IDF 5.3.2の環境で実行する。ターゲットは使用するESP32の機種へ変更する。GPIO指定は不要。

```bash
cd examples/transport_demo
idf.py set-target esp32
idf.py menuconfig
# Voice communication demoでSSID、パスワード、PCのIPv4、8766を設定
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

SSID・パスワードを含むsdkconfigやビルド成果物はGit除外。PCのTCPサーバーが起動済みである必要がある。無音だけのテストなので認識やTTSの動作確認にはならない。受信した返答音声を再生する機能も入れていない。

## サーバー側

[VOICE_TCP.md](../../../docs/api/voice-tcp.md) に接続仕様と起動手順を記載。startに `audio_profile: "pcm_s16le_16000"` を指定すると既存TTSの音声イベントをサーバーがPCMへ変換する。Androidは指定を省略するので従来の音声形式を維持する。TCPの分割・結合を考慮して4バイト長と本文を読み切り、session_id・generationで古い応答を拒否する。

現時点ではTCPサーバーは未起動（8766待受なし）、既存Webサーバーはactive。LAN公開の起動は以前の自動承認レビューで拒否され、今回も起動・再起動はしていない。

## 検証と未確認

サーバーの実ソケット試験と音声変換の13テスト、既存PCM関連の38テストを実施。ESP32用のESP-IDF／Cコンパイラーと実機はこの環境になく、Cコンポーネントと通信デモのビルド・書き込み・実機通信は未確認。実モデルを使ったTCP会話も未確認。GPIOやデバイスの制御は対象外。

参照した公式資料: [ESP-IDF v5.3.2 BSD Sockets](https://docs.espressif.com/projects/esp-idf/en/v5.3.2/esp32/api-reference/protocols/lwip.html)。外部実装コードのコピーはしていない。
