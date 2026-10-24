# Web画面とHTTP受付

ダッシュボードはブラウザーで使うWeb画面だけを指します。HTML・CSS・JavaScriptは `web/` に置き、`server.py` はそれらを配信するとともに、Web画面やAndroid WebViewが使う設定・参照WAV・音声セッションAPIのHTTPリクエストを受け付けます。音声処理と会話セッションのロジックは兄弟の [`STTLLMTTS/`](../STTLLMTTS/README.md) に委譲します。

## Web画面サーバーの起動

このPCではリポジトリルートの `./service-local.sh start` でバックグラウンド起動します。停止は `./service-local.sh stop`、再起動は `./service-local.sh restart` です。ターミナルで動かす場合は `./start-local.sh` を使います。LM StudioのモデルとCUDAライブラリも準備します。設定と確認結果は [LOCAL_SETUP.md](../../../LOCAL_SETUP.md) を参照してください。

依存関係をまだ準備していない場合は、リポジトリルートでOSに合うセットアップスクリプトを実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\STTLLMTTS\third_party\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1
```

Ubuntuでは次のように実行します。

```bash
bash Programs/ServerSide/STTLLMTTS/third_party/setup_ubuntu.sh
bash Programs/ServerSide/start_server_ubuntu.sh
```

起動スクリプトはServerSide直下の `.venv/` を使い、HTTPサーバーを前面で起動します。停止するには `Ctrl+C` を押します。既定の待受先は `127.0.0.1:8765` です。待受アドレスとポートはWindowsではパラメーター、Ubuntuでは位置引数で指定できます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Programs\ServerSide\start_server_windows.ps1 -BindAddress 0.0.0.0 -Port 8765
```

```bash
bash Programs/ServerSide/start_server_ubuntu.sh 0.0.0.0 8765
```

既定では `127.0.0.1:8765` のみにbindします。現在のサーバー開発と同じPC上のLM Studio・Irodori-TTS接続にTLSは不要です。サーバー画面を別PCから一時的に使う場合は、プライベートLANに限って `0.0.0.0` を指定できます。その場合は `Programs/ServerSide/.env` に32文字以上の `NENGITSUNE_DASHBOARD_TOKEN` を設定し、ファイアウォールで信頼するLANだけに制限してください。HTTPではトークンと音声が暗号化されないため、信頼できる開発ネットワークに限定し、インターネットには公開しないでください。

Android WebViewの音声会話は別段階です。現在のAndroidアプリはHTTPS以外を拒否し、WebのマイクAPIもLAN上のHTTPでは使えません。Android実機で音声往復を確認する際に、TLS終端を用意してください。

## Web APIと保存先

設定JSONと参照音声は `Programs/ServerSide/dashboard/data/` に保存され、Gitには含まれません。参照音声は固定名のPCM WAVのみを保存します。設定・参照WAV APIの詳細はサーバー要件書を、音声セッションAPIは [VOICE_API.md](../VOICE_API.md) を参照してください。

ダッシュボードで保存したVAD・STT・LLM・TTS・会話設定は、新しく作る会話パイプラインで使われます。保存済みの値は同じ項目の `.env` 値より優先されます。保存前は `.env` を初期値として読み、未指定ならSTTはCUDA / float16、LLM temperatureは0.7です。進行中の会話には後から反映されません。

現在の設定項目一覧、LLMの詳細設定、TTS合成後の出力音量、入力マイクと入力音量の扱いは [ServerSide利用マニュアル](../USAGE.md) を参照してください。

### 発話検出と区切り時間

「発話検出（サーバーVAD）」で感度の低・標準・高、話し終わりの待ち時間、最小発話時間、発話の最大長を保存できます。詳細設定では開始・終了しきい値と先頭に残す音声時間も調整できます。実際のSilero判定・発話分割はサーバーで行い、ブラウザーは無音も含めてPCMを送ります。STT欄のWhisper用VADとは別の設定です。

既定値は開始しきい値0.65・終了0.35、最小発話384ms、話し終わり待ち896ms、先頭保持640ms、最大20秒です。保存前は `VOICE_VAD_*` の環境値、保存後は画面の保存値を使います。既存の設定ファイルや古い画面からの保存でVAD項目が欠けていても、その時点のVAD設定を保持します。設定は会話開始時に固定し、保存時に進行中の入力・発話を中断しません。

### モデルと実際の稼働状態

「稼働状態」は認証付き `GET /api/v1/dashboard/runtime` を約10秒ごとに読み、次の会話用の設定値と現在の会話の観測値を分けて表示します。手動更新もでき、タブが非表示の間は定期取得を止めます。

STT・LLM・TTSについて、モデル名、確認できたデバイス・精度、TTSバックエンド、読み込み・処理・成功・失敗状態を表示します。最終成功は過去の実処理による確認であり、外部サービスの現在の接続を保証するものではありません。外部LLMやGradioのGPU配置は応答だけでは確認できないため、要求した設定と観測できた値を区別します。未使用・未観測の部品は「未確認」です。状態取得のためのモデルロード、推論、音声送信、サービス再起動は行いません。

Android Web向けのAPIは、状態取得、セッション開始、音声ターン送信、NDJSON逐次応答、旧クライアント向け返答音声取得、セッション停止を提供します。入力音声は検証後に処理用一時ファイルへ置き、処理後に削除します。逐次応答では相づちと文節単位のTTS音声をストリームし、クライアントが受信順に再生します。再生完了通知APIと実機再生は未確認です。

## 未確認事項

このPCでは既存APIのSTT・指定Gemma・ローカルTTS接続とブラウザー録音・再生を確認済みです。新PCM入力・サーバーVADと今回取り込んだNDJSONの統合後の実モデル確認、LAN・HTTPS、Android／ESP32実機での確認は残ります。入力継続のため現サービスと画面は再起動・再読込していません。詳細は `LOCAL_SETUP.md` を参照してください。
