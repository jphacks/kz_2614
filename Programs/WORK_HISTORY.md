# JPHACKS 制作作業・会話履歴

- 対象: `Programs/` 配下の「ねんぎつね」睡眠サポート作品
- 記録更新日: 2026-10-24
- 目的: 会話で受けた依頼、決定事項、作成・更新したファイル、未完了事項を後から引き継げるようにする。

> この記録は、このタスクで確認できる会話内容を時系列に整理したものです。過去の全発言を一字一句保存した逐語録ではありません。依頼文は確認できる範囲で原文に近く記載し、回答・作業内容は要約しています。

## 会話と作業の履歴

### 1. Programs 配下の README・プログラム資料

- 依頼: `C:\Users\ryuuz\Desktop\金沢工業大学\JPHACKS\Programs` 内の README に関する依頼。
- 対応: `Programs/README.md` を作品全体と実装構成を案内する資料として作成・更新。

### 2. 企画メモからプログラム資料を作成

- 依頼: 添付の「10月22日のメモ」の内容をもとに、作成するプログラムの Markdown ファイルを作る。
- 参照資料: `C:\Users\ryuuz\.codex\attachments\b0d99df9-a255-4cd9-97a0-ad0b1cbf69b3\貼り付けたテキスト.txt`
- 対応: 寝る時の寂しさを和らげることを中心に、会話、相づち、しっぽの動作、停止方法などを資料に反映。

### 3. 既存の AI_Secretary プログラムを確認

- 依頼: `C:\Users\ryuuz\Downloads\AI_Secretary-main\AI_Secretary-main` を確認し、使えそうなものを取り入れる。
- 対応: README、構成資料、音声処理や LLM 連携のコードを確認。短い音声向けプロンプト、会話履歴の制限、処理段階の分離などを参考にした。
- 整理方針: 自分たちのプログラムと他者のプログラムを分ける。自作側を `ServerSide/app/`、外部プログラムの置き場を `ServerSide/third_party/` とし、外部コードをそのまま自作コードとして扱わない。
- 注意: 外部リポジトリのコードを一式コピーしたものではない。ライセンスファイルの有無や各ライブラリの利用条件は、実際に再利用する前に別途確認が必要。

### 4. MCP によるしっぽ制御

- 依頼: 「MCPで尻尾を動かせる機構も入れておいてください」
- 対応: `ServerSide/app/tail_mcp_server.py` に MCP のしっぽ制御ツール案を作成。`tap_tail`、`stop_tail`、`get_tail_status` を用意し、設定が無効の状態を既定値にした。
- 制約: MCP サーバーから ESP32 に送る HTTP パスやペイロードはドラフト契約。対応ファームウェアや実機動作は確認していない。停止は LLM 応答待ちにしないこと、遅れて届いた制御を拒否することを要件に記載。

### 5. 相づちの要件

- 依頼: 「あいづちもしっかりmdに入れておいてね～」
- 後続の確認: 「あいづちを要件定義に入れていますか？」
- 対応: 相づちを要件として明記。1ターンあたりの重複防止、相づちと本返答の音声重複防止、停止・おやすみ時に再生しないことなどをクライアント・サーバー両方の資料に整理。
- 方針: 相づちは MVP に含める。相づち音声の生成・配信・再生制御は、資料上の要件がある一方で未実装。

### 6. Whisper 用フォルダと LM Studio 連携

- 依頼: 「ウィスパーを入れるためのフォルダとLMStudioと連携するファイルを作成して下さい。」
- 対応: faster-whisper の STT 用コード、LM Studio の音声会話クライアント、音声処理パイプライン、依存関係、環境設定例を `ServerSide/app/` に作成。Whisper モデル置き場には案内ファイルと空のプレースホルダーを用意。
- 主なファイル: `voice_stt.py`、`voice_llm.py`、`voice_pipeline.py`、`requirements.txt`、`.env.example`、`mcp.json.example`。
- 制約: Whisper モデル、Python パッケージ、LM Studio はこの作業でインストール・実行していない。TTS、相づち音声、スマートフォンへの音声配信機能も未完成。

### 7. クライアント・サーバー要件の確認

- 依頼: 「クライアント側の要件定義は大丈夫そうですか？」
- 依頼: 「サーバ側の要件定義は大丈夫ですか？」
- 対応: クライアントとサーバーの責務を分けた要件定義を作成し、停止、遅着データ、セッション、再接続、再生結果通知、相づち、しっぽ制御、安全制約、受け入れ条件などを整理。
- 追加確認への回答: MVP の音声入力・再生はどこで行うかについて、ユーザーが「MVPはスマホで録音・再生する」と決定。
- 決定した構成: スマートフォンが録音・再生を担当。サーバーが STT、LLM、TTS、音声返却、MCP しっぽ制御を担当する。

### 8. 履歴ファイルとプッシュ

- 依頼: 「適当にプッシュしてください。」
- 対応: `Programs/` 配下の19ファイルを `main` にコミットして `origin/main` へプッシュ。
- コミット: `c07f6ea` — `Add sleep companion program requirements`
- プッシュ後の確認: `main` と `origin/main` が同じコミットを指し、作業ツリーがクリーン。
- 現在の依頼: 「いままでの作業履歴を入れておくファイルを作成して今までの会話を入れておいてください。」
- 対応: 本ファイル `Programs/WORK_HISTORY.md` を作成。

### 9. STT → LLM → TTS パイプライン

- 依頼: 参照用の `AI_Secretary-main` を確認し、現在のプロジェクトに合う STT・LLM・TTS を作成する。
- 対応: 参照元は読み取りのみとし、Discord依存部を持ち込まず、Irodori-TTSのGradio API呼び出し方式を参考に `ServerSide/app/voice_tts.py` を追加。既存のWhisper STT・LM Studio LLMと `voice_pipeline.py` で接続し、生成音声のバイト列、形式、STT/LLM/TTS/合計時間を返すようにした。
- 関連変更: `app/requirements.txt`、`app/.env.example`、`app/README.md`、`ServerSide/README.md`、`ServerSide/REQUIREMENTS.md`、`ServerSide/.gitignore`、`voice_stt.py`、`Programs/README.md`。
- Whisper配置: 既存の `ServerSide/whisper/` をモデル保存先としてコード・Git除外設定と統一。
- 未確認: faster-whisperモデル、LM Studio、Irodori-TTS互換Gradioサーバー、GPU/CPU設定、生成音声の実機再生。スマホ向けAPI・音声配信・相づち・セッション停止は未実装。

### 10. エージェント作業指示リスト

- 依頼: 「Agent.mdの中に作業の指示リストを入れるように定義づけてください。」
- 対応: リポジトリ直下に `Agent.md` を作成し、作業ルール、音声プライバシー、しっぽ制御、未完了タスク、状態管理、完了条件を記載。Codexが自動参照する標準名 `AGENTS.md` を追加して `Agent.md` を案内。
- 作業リストは既存のクライアント／サーバー要件をもとに作成。STT/LLM/TTSのコード実装とモデル・端末を使った実機確認を別状態で扱う。

### 11. STT・LLM・TTSパイプラインと相づち

- 依頼: 参考プロジェクトをもとにSTT・LLM・TTSを実装し、相づちシステムも含める。
- 対応: STT音声入力にサイズ上限を追加。Irodori-TTS結果をメモリ上の音声データへ変換し、OS一時フォルダー内の取得ファイルを読み取り後に削除。2種類の相づち音声を事前生成・キャッシュし、LLM待ちが設定時間を超えた場合に最大1回だけ再生コールバックへ通知する処理を追加。
- 本返答は相づち再生コールバック完了後に返し、返答音声と重ならない契約にした。「おやすみ」・停止要求では相づちを抑止する。
- 主な変更: `voice_stt.py`、`voice_tts.py`、`voice_acknowledgements.py`、`voice_pipeline.py`、`.env.example`、ServerSide要件・説明資料、`Agent.md`。
- 確認: コードと要件の照合のみ。テスト、モデル接続、スマートフォン再生は未実施。
- 未確認: Whisper、LM Studio、Irodori-TTS各サービスへの接続、GPU設定、コールバックとスマートフォン再生の統合。スマホAPI・配信・再生完了通知は未実装。

### 12. ESP32との通信契約

- 依頼: 「このシステムにおいてESP32と通信するための決め事を決めたいですね。」
- 対応: `ServerSide/ESP32_API.md` を追加し、MVPでは隔離Wi-Fi LAN上のHTTP/1.1 JSON APIを使う契約を記録。APIパス、Bearer認証、タップ制限、成功／エラー応答、要求ID、タイムアウト・再試行の扱い、ファームウェア側の停止条件を定義。
- 関連更新: `Programs/README.md`、`ServerSide/app/README.md`、`ServerSide/REQUIREMENTS.md`、`Agent.md` の説明とタスク状態を同期。
- 未完了: 現行MCP HTTPクライアントのAPI契約への適合、セッション／停止世代を使った遅着開始の拒否、ESP32ファームウェア、手元停止と連続動作上限の実装・実機検証。制御設定は引き続き既定無効。

### 13. 音声パイプライン仕様と質問本文の入力先

- 依頼: 「仕様の内容を入れておいてください。ついでにちゃんと質問内容を入れておくところに入れておいてくださいね。」
- 対応: `ServerSide/REQUIREMENTS.md` にSTT→LLM→TTSと相づちの具体的な現行仕様、質問本文の受け渡し手順を追記。Whisper文字起こしをLM Studio `input` の「今回のユーザー発話（質問・依頼）」として渡し、過去の会話と `system_prompt` の役割を分離するよう `voice_llm.py` を更新。
- 関連更新: `voice_pipeline.py` に受渡しコメント、`ServerSide/app/README.md` に質問本文の流れを追記。
- 確認: コードと要件文書の照合のみ。LM Studio実接続・モデル回答の確認は未実施。

### 14. Raspberry Pi 4へデバイス機能を集約

- 依頼: ESP32ではなくRaspberry Pi 4を使い、ESP32に想定していた機能をPi 4側へ集約する。音声入出力とMCPのトントン制御を含める。
- 決定: Pi 4がマイク録音、スピーカー再生、しっぽのサーボ制御を担当する。スマートフォンは操作画面、アプリサーバーはSTT・LLM・TTSを担当する。
- 対応: `ServerSide/RASPBERRY_PI_API.md` に音声・サーボAPI案を記録し、ClientSide／ServerSide要件、README、Agent作業リストを更新。
- 未完了: Pi 4デバイスサービス、音声機器と形式・上限値、現行MCPクライアントのAPI適合、停止世代の拒否、GPIO・サーボ回路と手元停止、実機検証。
- ブランチ: 実装用に `codex/raspberry-pi-implementation` を作成。Markdown類は `main` に反映し、実装コードは作業ブランチに保持する。

### 15. Android Webアプリとサーバー設定ダッシュボード

- 決定: ユーザーは「androidはwebアプリを基本とします」と指定。さらに、WebのHTMLなどをサーバー側へ置き、ダッシュボードにして、STT音声・TTS・会話時のふるまいを設定したいと指定。
- 対応: `ServerSide/web/` にモバイル対応の設定画面（HTML/CSS/JavaScript）を作成。STTモデル・言語・実行デバイス・計算形式・VAD、TTSモデル・声の説明、話し方・返答長・質問頻度・追加指示を編集できる構成にした。相づちは必須で無効化できない。
- 対応: `app/dashboard_server.py` に静的ファイル配信と設定読込／保存APIを作成。既定はloopback bind、loopback外では管理トークンを必須とし、設定値を `app/data/dashboard_settings.json` に保存する。設定ファイルはGit除外。
- 対応: `app/settings_store.py` を追加し、STT・TTS設定を新しい処理インスタンスに、ふるまいの希望を安全ルールに添えたシステムプロンプトに反映する。ダッシュボードからMCPの許可状態は変更しない。
- 関連更新: `ClientSide/README.md`、`ClientSide/REQUIREMENTS.md`、`ServerSide/README.md`、`ServerSide/REQUIREMENTS.md`、`ServerSide/app/README.md`、`Programs/README.md`、`.env.example`、`.gitignore`、`Agent.md`。
- 未確認: ダッシュボードの起動、設定保存・再読込、モデルへの反映、LAN/HTTPSアクセス、Android端末での表示・操作は未確認。音声会話の録音・送信・再生APIは別途未実装。

### 16. Irodori-TTS参照WAVのアップロードと保存

- 依頼: 「音声設定がなくねwavファイルなどをIrodoriTTSは設定できるのでwavを保存する機構とそれに対応するwebダッシュボードを実装しておいてくださいね。」
- 対応: ダッシュボードのTTS欄にWAV選択、サーバーへの保存、状態表示、削除を追加。APIは `GET`／`PUT`／`DELETE /api/v1/dashboard/reference-audio`。
- 保存と検証: PCM WAV、16-bit、mono/stereo、16〜48kHz、1〜30秒、10MB以下を受け入れる。固定先は `ServerSide/app/data/reference_voice.wav`。原子的に置換し、ファイル名や保存先をブラウザーから受け取らない。設定ファイルと参照音声はGit除外。
- TTS連携: 新しい `IrodoriTTS` インスタンスが保存音声を読み込み、Gradioの `ref_wav` として送る。インスタンス作成後に保存音声を置換・削除しても、既存インスタンスは読み込み済みの音声を使う。
- 未確認: アップロードAPI、破損WAV拒否、削除、実際のIrodori-TTS接続と生成、Androidブラウザーからの操作は未確認。MVPのWebアップロード形式はPCM WAVのみ。

### 17. 文書と実装のブランチ分離（2026-10-24）

- 依頼: Markdown文書は `main` に置き、ユーザーの実装部分は作業ブランチにまとめる。
- 対応: Pi 4のAPI契約と関連要件・作業ガイドを `main` の `1d827fb` に記録。`codex/raspberry-pi-implementation` に `main` を統合し、ダッシュボード、音声セッション／処理、Android WebViewクライアントを `a3dbef8` と `65bc232` に保存。
- 確認: 文書と実装の差分チェックは成功。設定例に実際のAPIキーらしき値がないことを簡易確認。テスト、ビルド、Android・Pi 4実機確認は未実施。
- 未完了: Pi 4のデバイスサービス、音声機器との接続、MCPしっぽ制御のAPI整合と実機確認は未完了。機能ブランチはローカルに保存し、リモートへは未送信。

### 18. Android WebViewとターン制音声会話（2026-10-24）

- 依頼: `Programs/ClientSide/androidVer` にAndroid Webアプリを開く機能と、最低限の音声通話機能を追加し、音声通話の仕様はServerSide実装を見ながら整合する。
- 決定: Android WebViewがサーバー画面を開き、スマートフォンのマイクで1発話ずつ録音し、サーバーがSTT→LLM→TTS、スマートフォンが返答を再生する。Pi 4のしっぽ制御は別経路とし、音声入出力に使わない。
- Android: `MainActivity.java` でサーバーURL入力・保存・再起動時の再表示、同一オリジンだけのWebViewマイク許可を追加。録音・インターネット権限と起動ActivityをManifestに設定。
- Web: `ServerSide/web/` に会話開始・録音・送信・返答表示／再生・即時停止UIを追加。HTTPSとマイク拒否の案内、60秒録音上限、停止時のローカル録音・再生停止を実装。
- Server: `app/voice_sessions.py` と `dashboard_server.py` にセッション・状態・raw audio upload・期限付き音声取得・停止APIを追加。最大4セッション、1セッション1発話、15分期限、25MB・60秒入力上限、最大180秒処理期限。停止時は世代を進めて音声を破棄し、処理タスクへ取消を通知する。
- データ方針: 入力音声はOS一時領域にのみ置いて処理後に削除。返答音声はメモリ内で2分まで、停止・期限切れで破棄。ログに音声本文を出さない。
- 文書: `ServerSide/VOICE_API.md` を追加し、ClientSide／ServerSide要件、README、`Agent.md` の作業状況を更新。
- 確認: Androidビルド、サーバー起動、API・ブラウザー動作、モデル接続、実機確認は実施していない。既存変更は保持し、テストは未実施。
- 未完了: Android実機のHTTPS・権限・録音・再生、Whisper/LM Studio/Irodori-TTSの接続、相づち配信、再生完了通知、切断・停止競合の受け入れ確認。Pi 4音声機器連携はこのAndroid Web経路に含めない。

### 19. Android録音競合の抑止とHTTPS必須化（2026-10-24）

- 対応: 音声画面でマイク権限待ち中と再生中の録音開始を抑止し、セッション停止後に遅れて取得したマイクストリームを直ちに解放する。録音エラー時はタイマーと一時データを破棄する。
- セキュリティ: Android WebViewの接続先をHTTPSに限定し、Manifestの平文HTTP許可を削除した。
- 確認: 差分チェックのみ。Androidビルド、ブラウザー動作、実機確認は未実施。
- 未完了: Android端末でHTTPS、マイク権限、再生中の誤操作、停止と録音要求の競合を確認する。

## 現在のファイル構成

```text
Programs/
├── AGENTS.md
├── Agent.md
├── README.md
├── WORK_HISTORY.md
├── ClientSide/
│   ├── README.md
│   ├── REQUIREMENTS.md
│   └── androidVer/
│       ├── README.md
│       ├── app/src/main/AndroidManifest.xml
│       └── app/src/main/java/com/example/jphackskz2614androidver/MainActivity.java
└── ServerSide/
    ├── README.md
    ├── RASPBERRY_PI_API.md
    ├── VOICE_API.md
    ├── REQUIREMENTS.md
    ├── web/
    │   ├── index.html
    │   ├── dashboard.css
    │   ├── dashboard.js
    │   └── voice_call.js
    ├── .gitignore
    ├── app/
    │   ├── README.md
    │   ├── __init__.py
    │   ├── dashboard_server.py
    │   ├── voice_sessions.py
    │   ├── mcp.json.example
    │   ├── requirements.txt
    │   ├── settings_store.py
    │   ├── data/                 # 設定JSON・参照WAVの実行時保存先（Git除外）
    │   ├── voice_acknowledgements.py
    │   ├── tail_mcp_server.py
    │   ├── voice_llm.py
    │   ├── voice_pipeline.py
    │   ├── voice_stt.py
    │   └── voice_tts.py
    ├── third_party/
    │   ├── README.md
    │   └── Irodori-TTS/.gitkeep
    └── whisper/
        ├── README.md
        └── .gitkeep
```

## 現在の決定事項

- 更新後のAndroid Web MVPではスマートフォンが録音・再生、サーバーがSTT・LLM・TTSと会話セッションを担当する。Pi 4のしっぽ制御は別経路とする。
- AndroidはWebViewでサーバーのWebアプリを開き、画面のHTMLなどは `ServerSide/web/` から配信する。
- STT・TTS・会話のふるまいはサーバーのダッシュボードから設定し、サーバー側に保存する。設定は新しい音声処理インスタンスから反映する。
- Irodori-TTSの参照音声はダッシュボードからPCM WAVをアップロードし、サーバー内の固定パスに保存する。ユーザーは置換・削除できる。
- サーバー側で STT、LLM、TTS と返答音声配信を行う。Android Webは1発話ごとに録音・送信・再生する。
- 相づちは MVP 要件に含める。
- しっぽ制御は MCP 経由とし、無効設定を既定値にする。動作にはユーザーの明示的な依頼や制御条件を必要とする。
- 「おやすみ」の会話上の終了と、UI の即時停止を区別する。即時停止は LLM の応答完了を待たない。
- 自作コードと外部プログラムを別の場所に置く。
- 2台連携、睡眠状態推定、通知、長期記憶などは MVP の対象外として扱う。

## 未完了・未検証事項

- 設定ダッシュボード、設定保存API、PCM WAV管理、Android WebView、ターン制の録音・送信・再生UI、セッションAPIのコードを追加したが、起動・Android実機・HTTPS接続・モデル利用は未確認。
- Irodori-TTS呼び出しクライアント、STT→LLM→TTSパイプライン、相づちキャッシュ、返答音声配信は実装コードがある。Android Webへの相づち配信と再生完了通知は未実装・未確認。
- セッション停止・世代フェンス・期限を追加したが、外部モデル処理の実取消、切断・再接続、遅着LLM/MCP動作拒否は未確認。
- `mcp.json.example` はこのPCの絶対パスを含む。別のPCで使う際にパスを変更する。
- Raspberry Pi 4デバイスAPI案は `ServerSide/RASPBERRY_PI_API.md` に文書化済み。音声端点はAndroid Web MVPでは使わず、MCPしっぽ制御のAPI整合、セッション停止世代、デバイスサービス、モーターの電源・角度・速度制限は未解消・未検証。
- 音声形式、録音時間・サイズ上限、各処理のタイムアウト、相づちを出すタイミング、バックグラウンド動作条件は未確定。
- 実機、Whisper モデル、LM Studio、TTS、スマートフォンでの受け入れ試験は未実施。要件書にある受け入れ条件を実施済みとみなさない。

## 履歴の追記方法

今後の作業では、依頼や重要な決定、変更したファイル、実施した確認、残作業を日付付きでこのファイルに追記する。未実施の確認を実施済みとして記録しない。
