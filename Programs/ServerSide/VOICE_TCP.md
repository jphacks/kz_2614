# Androidネイティブ音声通話（2026-10-24）

AndroidのWebViewを廃止し、AudioRecordとMediaPlayerを使う音声通話専用アプリへ変更した。アプリからサーバーのTCPポートへ直接接続し、HTTP、ブラウザー、WebSocket、HTTPS終端は使わない。既存Web画面はサーバー管理・既存利用のため残す。人同士の電話ではなく、既存STT → LLM → TTSとの音声会話。

## 起動と接続

Ubuntuではリポジトリルートで以下を実行する。Windowsは `Programs/ServerSide/start_voice_tcp_windows.ps1 -BindAddress <PCのLAN IP> -Port 8766`。ServerSideの既存venv・`.env`・設定・参照WAVを使う。LM StudioとTTSバックエンドは事前に既存手順で起動しておく。

```bash
bash Programs/ServerSide/start_voice_tcp_ubuntu.sh --host 192.168.137.201 --port 8766
```

IPは現在のPCのLAN IPに置き換える。既定は127.0.0.1:8766。LANでは同じWi-FiのスマホからPC IPと8766を指定する。プライベートLAN向けの認証なし・暗号化なし通信のため、インターネットへ公開しない。ファイアウォールで許可する場合は同じLANのTCP 8766に限定する。

Android Studioで `Programs/ClientSide/androidVer` を開き、Debug APKをビルド・インストールする。接続先はURLではなくIPアドレスまたはホスト名とポート。通話開始時にAndroidのマイク許可を求める。停止、画面を離れる、通信断では録音・再生を止める。自動再接続はせず、新しい通話として手動開始する。

既存HTTPサービスを再起動せず別プロセスで起動できる。ただしセッションとモデルインスタンスは別で、両方で同時に会話するとGPUメモリを追加使用する。TCP通話を通常運用する際は、利用者が既存Web通話を終えてから切り替える。サーバーの設定変更UIは既存Web画面を使う。

## TCP v1契約

1接続＝1セッション。各メッセージは **4バイト符号なしビッグエンディアンのJSONバイト長 + UTF-8 JSON**。TCPの分割・結合を前提に指定長を読み切る。クライアント要求は最大48,000 bytes、応答は最大24MiB。サーバーの同時接続上限は4、読取・送信タイムアウトは15秒。再送／接続再開によるセッション復元は行わない。

| 方向 | JSON | 動作 |
| --- | --- | --- |
| client → server | `{"type":"start","version":1}` | 最初の要求。サーバーが新規会話を作る |
| server → client | `{"type":"session","session_id":"UUID","generation":0,...}` | 開始成功。失敗時はerrorと切断 |
| client → server | `{"type":"pcm","sequence":0,"audio_base64":"..."}` | PCM16LE、16kHz、mono。1〜32,000 bytes、偶数長、連番は0から |
| server → client | `{"type":"state","session_id":"UUID","generation":0,"stage":"listening",...}` | 各PCMのACKと途中認識状態 |
| server → client | `transcript / text_delta / acknowledgement / audio_chunk / complete` | VOICE_API.mdと同じ既存イベント。JSON内typeで識別。音声はmedia_typeとaudio_base64 |
| client → server | `{"type":"playback","turn_id":"UUID","event":"started"}` | 最初の文節の実再生開始。終了はended、失敗はfailed |
| client → server | `{"type":"stop"}` | セッション廃棄と切断。ソケット切断でも同じ処理 |
| server → client | `{"type":"error","error":"code","message":"説明"}` | 通話終了。手動で再開 |

開始時に受け取ったsession_id/generation以外の応答をアプリは破棄する。PCMは無音・生成・出力中も継続送信し、VAD・発話分割はサーバーへ委譲する。確かな発話開始時の`interruption`で旧返答を打ち切り、新発話へ切り替える。アプリにVADやしっぽ操作・設定管理は持たせない。入力と出力は同じTCP接続の独立スレッドで扱う。端末実装は`interruption`で旧音声buffer・通知を破棄する必要があり、旧Android MediaPlayer実装での割込み対応・実機確認は未完了。

音声はサーバーで既存の処理用一時WAV以外に保存しない。AndroidではMediaPlayerに渡すためアプリ専用キャッシュへ文節音声を一時作成し、再生終了・失敗・停止で削除する（OSによるプロセス強制終了では残る可能性がある）。アプリ再起動時にも残存ファイルを削除する。再生待ち上限は8件、各音声のbase64上限は2MiB。過負荷時は音声を黙って欠落させず通話を終了する。

## 確認範囲

モデルをモックした実TCPソケットの7テストで、PCM転送、独立した返答配信、再生通知、停止・切断時の廃棄、分割受信、不正長・不正PCM・プロトコル版の拒否を確認。Android SDK/JDK未導入のためAPKビルド・実機の録音／再生／停止／Wi-Fi切断は未確認。TCP経由の実モデル往復も未確認。既存Webサービスの再起動・入力停止は行っていない。

このPCの稼働反映: `192.168.137.201:8766` での継続的なLAN待受起動は自動承認レビューが拒否したため、まだ起動していない。明示承認後に実行する。既存HTTPサービスは稼働を維持。起動後の停止コマンドは `systemctl --user stop nen-gitsune-voice-tcp.service`（このサービス名で登録した場合）。

## ESP32クライアント向け追加（2026-10-24）

ESP32の通信部品は [ESP32Voice](../ClientSide/ESP32Voice/README.md)。GPIO・音声機器制御はユーザー指定で対象外。

最初の要求に `{"type":"start","version":1,"audio_profile":"pcm_s16le_16000"}` を指定する。session応答は同じaudio_profileを返す。既存Androidは省略可能で `original` と応答する。未知のprofileと不正versionはセッション作成前に拒否する。

ESP32 profileのacknowledgement/audio_chunkは `media_type: "audio/pcm"`、`sample_rate: 16000`、`channels: 1`、`format: "s16le"`、PCMのaudio_base64を返す。PyAVで既存TTS音声を復号・ダウンミックス・リサンプルする。文節の追加分割はしない。音声の上限は512KiB、パケット上限は1MiB。超過はdevice_audio_too_large、変換失敗はdevice_audio_conversion_failedで終了する。AndroidとWeb向けの元イベントやTTS生成処理は変更しない。

サーバー側のTCP／変換13テストと既存PCM関連38テストで確認。ESP32のコンパイル・実機、TCP実モデル往復は未確認。8766は未待受、既存Webサービスはactiveを今回の読み取り確認で検出した。以前の起動拒否を回避する操作は行っていない。

## 連続PCM出力profile

`start`に`audio_profile:"pcm_s16le_16000_stream"`を指定すると、親文節を100ms以下の`audio_frame`として送ります。PCM形式・frame番号・親cursorは[Web音声API](VOICE_API.md)と共通です。TCPは切断後に旧sessionを復元せず、新規接続のstartで新しいsessionを作ります。Webのcursorによる再接続・再送をTCPの機能として扱わないでください。各frameはファイルではないPCM16LE/16kHz/monoで、端末は小さな音声bufferへ入れ、受信と出力を並行させます。モデルの文節合成方式は変更しません。

`interruption`はframeへ分割せず送信します。旧turn_idのbufferと遅着音声を捨て、新しいturn_idを受理します。割込み済み旧turnの再生通知で`playback_turn_mismatch`が返った場合に限り、その通知を無視して接続を維持します。未知のturn・世代不一致などは従来どおりエラーです。サーバーの実ソケット試験で確認し、ESP32・Android側のこの新profile対応と実機確認は別作業です。
