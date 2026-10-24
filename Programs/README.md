# 添い寝ロボット「ねんぎつね」（仮）— Programs

寝る前の寂しさに寄り添う、きつね型の添い寝クッションを動かすソフトウェアの概要と実装方針です。名前・構成・モデルは検討中の案を含み、確定していない項目はその旨を記載しています。

更新後のAndroid Web MVPではスマートフォンが音声入出力と操作画面、アプリサーバーがSTT・LLM・TTSと会話セッション管理を担当します。Raspberry Pi 4はMCP経由のしっぽ制御先として扱います。ダッシュボードはブラウザーで使うWeb画面を指し、画面を `ServerSide/dashboard/web/` に置きます。`dashboard/server.py` はWeb画面とクライアント向けのHTTP窓口、音声処理と会話制御は `ServerSide/STTLLMTTS/` が担当します。必須動作と現行コードとの差分は各要件書にまとめ、実装・受け入れ確認と区別します。

現フェーズの運用方針（2026-10-24に決定）: GPU搭載のローカルPCでWhisper・LM Studio・Irodori-TTSを使用し、Androidから同一LocalNET内のサーバーURLへ接続して音声入力・音声出力を行います。実際の接続先URL、HTTPS終端、モデル設定と実環境での動作は未確認です。しっぽ制御は音声会話の初期MVPの後に進めます。

## 目指す体験

ユーザーがAndroid画面から会話を始め、スマートフォンのマイクに話しかけます。返答音声はスマートフォンで再生し、明確にトントンを求めるとPi 4につないだ柔らかいしっぽが動きます。停止ボタンは最後の返事を待たず端末の音声処理と会話セッションを停止します。

電子機能が使えないときも、枕として使えることを基本にします。まず1台で会話・トントン・停止までを通し、2台連携はその後に検討します。

## 最小構成（MVP）

- 枕として使える本体と、柔らかいしっぽ
- 短い音声会話を1往復できること
- 返答待ちに、発話を遮らない短い音声の相づちを返せること
- 会話に合わせてしっぽを小さくトントンさせられること
- ユーザーが手元で停止できること
- 通信切断やタイムアウト時に、しっぽを停止できること
- 電子機能を停止しても枕として使えること

睡眠時間の記録、眠気の推定、夕方の通知、長期記憶、細かな人格設定、2台連携はMVPの後に検討します。

## プログラムの構成案

このリポジトリの `ClientSide` と `ServerSide` を、次の役割に分ける想定です。Android Web音声APIを [音声会話API仕様](ServerSide/VOICE_API.md)、Pi 4デバイスAPI案を [Raspberry Pi 4 API仕様](ClientSide/Raspi4Ver/RASPBERRY_PI_API.md) にまとめました。音声会話APIコードはありますが実機確認は未完了です。Pi 4の音声API案はAndroid Web MVPの音声経路では使用しません。

### 開発作業の担当分け

| 作業 | 主な担当範囲 | 役割 |
| --- | --- | --- |
| Androidクライアント | `ClientSide/` | WebView、サーバーURL、マイク権限、録音・再生、停止操作 |
| Webダッシュボード（画面） | `ServerSide/dashboard/web/` | ブラウザー上の設定・音声会話の操作画面 |
| WebアプリのHTTP窓口 | `ServerSide/dashboard/server.py` | Web画面の配信と設定・音声会話APIの受付。音声処理は `STTLLMTTS/` に委譲 |
| STT | `ServerSide/STTLLMTTS/stt/` | faster-whisper接続と文字起こし |
| LLM | `ServerSide/STTLLMTTS/llm/` | LM Studio接続、質問本文と履歴の受け渡し |
| TTS | `ServerSide/STTLLMTTS/tts/` | Irodori-TTS接続と音声生成 |
| 音声オーケストレーション | `ServerSide/STTLLMTTS/voice/` | STT→LLM→TTSパイプライン、会話セッション、相づち |
| 共有設定 | `ServerSide/dashboard/settings_store.py` | Web画面から保存した設定を音声処理へ提供し、参照WAVを検証・管理 |
| Raspberry Pi 4・しっぽ | `ServerSide/STTLLMTTS/tail_mcp_server.py`、`ClientSide/Raspi4Ver/RASPBERRY_PI_API.md` | MCPから機器APIを呼ぶ。初期音声MVPの後に実装・統合 |

### ブランチ運用

作業は `main` から用途ごとの `codex/<担当>-<作業>` ブランチを作って進めます。次の名称は作業単位の例であり、事前にブランチが存在することを示すものではありません。

| ブランチ名の例 | 主な作業 |
| --- | --- |
| `codex/android-client` | Android WebView、マイク、録音・再生、端末側停止 |
| `codex/server-dashboard` | Webダッシュボード画面とその配信 |
| `codex/server-stt` | faster-whisper接続と文字起こし |
| `codex/server-llm` | LM Studio接続と質問本文・履歴の連携 |
| `codex/server-tts` | Irodori-TTS接続と音声生成 |
| `codex/server-voice-pipeline` | モデル接続を組み合わせるパイプライン、セッション、相づち |
| `codex/raspberry-pi-tail` | Pi 4デバイスサービス、MCP、しっぽ制御 |

担当をまたぐ変更では、先に `VOICE_API.md` または `ClientSide/Raspi4Ver/RASPBERRY_PI_API.md` と関連要件書を更新し、同じAPI契約に合わせます。作業ブランチで差分を確認してから `main` に統合します。`main` は統合先とし、個別作業を直接積み重ねません。

### ClientSide（Androidブラウザーを基本とするスマホ画面）

- 接続先と接続状態の表示
- Androidマイクによる会話開始／停止と端末音声状態の表示
- 本返答の再生と、重複・遅着音声の確認。相づちと再生結果通知は今後の対応
- 会話・トントンの開始／停止
- 音量、トントンの有効／無効と速さの設定
- 設定の適用確認と、停止未確認・切断・再生失敗の表示
- 声、キャラクター、話す頻度の設定（MVPでは後回しでもよい）
- Androidスマートフォンを優先して動作確認

設定・音声会話の操作を行うWeb画面は `ServerSide/dashboard/web/` に置きます。画面から設定やIrodori-TTS用の参照WAVをサーバーAPI経由で保存・削除でき、形式・長さ・容量の検証はサーバー側で行います。ダッシュボード画面自体は音声処理を行わず、STT・LLM・TTSと会話制御は `ServerSide/STTLLMTTS/` が担当します。Android WebViewの録音・送信、NDJSONによる相づち・返答TTSの逐次配信と順次再生、セッションAPIのコードを追加しました。HTTPS環境、再生結果通知API、Pi 4のMCPしっぽデバイスサービス、Android実機確認は未完了です。

### ServerSide（会話処理・連携の案）

- `dashboard/web/` のブラウザー設定・会話操作画面
- `dashboard/server.py` によるWeb画面の配信と設定・音声セッションAPIのHTTP受付。音声処理・会話制御は `STTLLMTTS/` に委譲

- Android Webからの音声受信、STT→LLM→TTSの処理、返答音声の配信
- 会話セッションの開始・終了、期限・停止世代の管理
- 会話セッションの開始・終了管理
- LLMからMCP経由でしっぽの開始・停止・状態確認を呼び出す
- MCPサーバーからRaspberry Pi 4への固定動作指示（HTTPS/JSON API）
- 失敗・切断・タイムアウト時の停止処理

サーバーコードは `ServerSide/STTLLMTTS/` とWebアプリ層の `ServerSide/dashboard/` に分けています。ダッシュボードはブラウザー画面を指し、`dashboard/server.py` はその配信とHTTP受付を行います。`STTLLMTTS/voice/pipeline.py` がモデル接続をつなぎ、`voice/sessions.py` が会話セッションを管理します。モデル・HTTPS環境・Android実機は未確認です。詳細は [ServerSide利用マニュアル](ServerSide/USAGE.md)、[ServerSideの構成](ServerSide/README.md)、[音声API](ServerSide/VOICE_API.md)、[STTLLMTTS](ServerSide/STTLLMTTS/README.md)、[Web画面](ServerSide/dashboard/README.md) を参照してください。

モデル名は引き続きコードの初期設定・候補値です。ユーザー方針としてGPU搭載PC上でWhisperとLM Studioをローカル利用し、TTSにIrodori-TTSを使います。モデルの実配置、GPUデバイス設定、接続先、起動、応答速度は未確認です。

### Raspberry Pi 4・しっぽ機構

- SG90サーボで軽い布・スポンジ製のしっぽを小さく上下させる
- サーボは外部5V有線給電とし、Pi 4のGNDと共通化する。GPIOからサーボ電源を取らない
- 電源を切れる手元停止と、Pi 4側の動作タイムアウトを設ける
- 0〜12°はCAD上の表示例であり、実機の角度・押す強さ・騒音・発熱は実物で調整する

Pi 4のデバイスサービス、サーボ信号回路、しっぽの動作上限と手元停止は未確定です。[Raspberry Pi 4 API仕様](ClientSide/Raspi4Ver/RASPBERRY_PI_API.md) には将来の機器API案も記録していますが、Android Web MVPの音声はスマートフォンで録音・再生します。

## データの流れ（概念）

```text
Android WebViewマイク → サーバー(STT → LLM → TTS) → Android WebViewスピーカー
                                             │
                                             └─ MCP → tail_mcp_server → HTTPS → Pi 4 GPIO → SG90
スマホの設定・会話操作 → アプリサーバー
```

音声はAndroid WebViewからサーバーへ1発話ずつ送り、サーバーで無音区間に分けてWhisperに渡します。Whisperは信頼度・声の高さ・話し方を分析し、音響特徴の要約をLLM入力に加えます。返答テキストはLM Studio SSEから逐次受け取り、文節ごとにTTS音声をNDJSONで配信します。セッション・発話・停止世代を使い、停止後の遅着結果を拒否します。再生結果通知API、Pi 4しっぽデバイスサービス、実機確認は未完了です。

## 動作の流れ

1. ユーザーがAndroid画面で開始し、マイク権限とサーバー接続を確認する。
2. Androidの録音をサーバーへ送り、サーバー側で無音区間を分けてSTTへ渡す。声の特徴も分析し、文字起こしと一緒にLLM入力へ含める。
3. サーバーは返答の先行テキストをTTS文節にして逐次配信する。最初の返答音声が遅いときは事前生成した相づちを1回配信し、Androidが受信順に再生する。
4. Androidのスピーカーで返答音声を順番に再生する。明示的なトントン要求はサーバー側の会話制御で検証し、サーバー側MCPプロセスからPi 4のしっぽを動かす。Androidはサーバー向け要求と結果表示を行い、Pi 4へ直接接続しない。
5. 「おやすみ」は最後の短い返事で終了する。停止ボタンは即時に端末音声と会話セッションを止め、しっぽ停止は独立した制御要求として扱う。
6. 通信断、異常、またはタイムアウト時は、しっぽを停止できる状態にする。

音声の開始条件、返答中のトントン動作、タイムアウト時間などの細部は実機統合時に決定します。

## 相づち（MVP必須要件）

返答を待つユーザーに、聞いていることを伝える短い音声の相づちを提供します。相づち機能自体は必須とし、再生タイミングや使用する言葉は実機で調整します。

- ユーザーの発話中には再生せず、発話が終わってから返答を待つ間に使う。
- LLMの返答を待たせる場合に、短い相づちを1回だけ再生する。返答音声と重ねない。
- 「うん」「そっか、聞いてるよ」など安心感のある短い候補を用意し、同じ言葉を連続で繰り返さない。
- 夜間に大きな音で驚かせないよう、相づちも設定音量に従う。停止・おやすみ操作後は再生しない。
- AI_Secretaryに合わせ、相づち候補を会話開始後に事前生成・キャッシュし、最初の返答音声の準備が設定時間を超えた場合だけ1回配信する。
- LM StudioのSSEから返答差分を受け、文節単位のTTS音声をNDJSONでAndroidへ逐次配信する。Androidは受信順に再生キューへ入れる。

相づちの選択・候補音声の事前生成・LLM待ち時間の判定はサーバー側の責務です。候補音声の逐次配信とAndroid Webの順次再生コードを追加しました。再生開始・完了・失敗通知APIと実端末の停止確認は未実装です。

受け入れ条件は、返答待ちが設定時間を超えた際に1往復につき最大1回だけ再生すること、ユーザーの発話や本返答に重ならないこと、停止操作で再生中・待機中の相づちを停止できることです。本返答が先に準備できた場合は相づちを省略します。

## 実装・確認の順序

1. GPU搭載ローカルPCでWhisper・LM Studio・Irodori-TTSを準備し、STT → LLM → TTSの音声1往復と返答時間を確認する。
2. Android WebViewからLocalNET上のServerSide URLへ接続し、HTTPSで録音・送信・音声再生を確認する。
3. 初期音声MVPとして、相づち、停止、無音・破損音声、切断・再接続を実機で確認する。
4. 初期音声MVPの後にサーバー側でPi 4デバイスサービス・しっぽAPIとの接続を実装し、MCP Inspectorからタップ・停止・状態取得を確認する。Androidはサーバー向け要求と状態表示だけを担当する。
5. LM StudioへMCPサーバーを登録し、明示的な「トントンして」でだけツールを呼ぶことを確認する。
6. 音声会話とPi 4のしっぽ制御を統合し、停止・再起動・通信切断からの復帰を確認してから、2台連携を検討する。

## 仕様を確定する項目

- Androidブラウザーの録音形式、再生形式、API上限に対する対象端末の対応状況
- Android WebViewとサーバー間のHTTPS接続、音声API認証、通知・再接続方法
- セッションと停止世代の識別、遅着結果とMCP動作を拒否する方法
- MCPとPi 4でセッション許可・停止世代を連携し、遅れて届く開始要求を拒否する方法
- Raspberry Pi 4側の角度制限、ウォッチドッグ、電源遮断式の停止手段をどう実装・検証するか
- Pi 4の電源容量、GPIO信号回路と配線
- サーボの実機動作範囲、しっぽの接触位置、停止時間
- STT／LLM／TTSの採用モデル、サーバー・GPU環境、応答速度
- 設定項目、通信メッセージ、音声形式、切断時の再接続方法
- 2台連携をデモの必須要件とするか
- ロボットの正式名称、キャラクターの口調

## デモの基本シナリオ

音声会話の初期デモは「今日はちょっと寂しい」→Androidスマートフォンへ話す→短い返事を音声で再生→「おやすみ」→短い返事のあと静かに停止する、の流れとします。Pi 4のしっぽ制御は後続フェーズで追加します。

入眠する場面はデモ上の演出です。会話と動作が使えない場合でも、枕として使える状態を保ちます。

## 現在のディレクトリ

```text
Programs/
├── ClientSide/   # Android Webクライアントの要件
├── ServerSide/
│   ├── USAGE.md               # Windows・Ubuntuの利用マニュアル
│   ├── STTLLMTTS/         # STT・LLM・TTS・音声パイプライン・セッション
│   │   ├── tail_mcp_server.py
│   │   ├── mcp.json.example
│   │   └── third_party/
│   ├── start_server_windows.ps1
│   ├── start_server_ubuntu.sh
│   ├── dashboard/         # Web画面とWebアプリ用HTTPサーバー
│   │   ├── server.py
│   │   ├── settings_store.py
│   │   ├── web/
│   │   └── data/          # 設定JSON・参照WAV（Git除外）
│   ├── requirements.txt
│   ├── .env.example
│   ├── whisper/           # Whisperモデルのキャッシュ先
│   └── README.md          # 自作／外部の区分
└── README.md     # 全体の目的、MVP、構成案、未決事項
```

参照WAVと設定ファイルは `ServerSide/dashboard/data/` 内に保存され、Gitには含まれません。

この文書は2026年10月22日の企画メモをもとにした実装開始用の整理です。未確認の案は、実機・通信・モデルで確認するまで確定仕様として扱いません。
