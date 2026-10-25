# 製品Webアプリの起動・配信

対象は [apps/hossy-web](../../apps/hossy-web/README.md)。8765番の音声管理Webとは別の入口で、同じ共有音声クライアントを使います。[仕様](../requirements/web-product.md) / [使い方](../web/usage.md)。

## 公開先と確認状況

[公開ホーム](https://hiroron-jphacks2026.hiroron.net/) / [ダイアリー](https://hiroron-jphacks2026.hiroron.net/#diary)。公開URLは利用者指定です。未ログインの `GET /api/v1/voice/status` は401・text/htmlで、Passport認証後のUI・API転送先・実際の配信版は未確認です。ログイン画面への到達を音声API成功とは扱いません。

ブラウザーは同一originの `/api/v1/voice/` と `/api/v1/dashboard/runtime` にアクセスします。配信サーバー側からPC音声サーバーへ転送する経路を担当者と確認してください。公開HTTPSからブラウザーがPCのHTTPへ直接接続する設定にはしません。音声サービスはPCのHTTP 8765番と定義済みで、公開配信側の具体的なupstream設定と到達性は未確認です。この変更は公開サイトへのデプロイ・認証設定・サーバー再起動を含みません。

## ローカル開発

音声のAPIパス`/api/v1/voice/`は固定です。一方、音声PCのIP/ホスト名・ポートは環境ごとの設定です。Webが別PCで動く場合、`127.0.0.1`はそのWebのPCを指します。他メンバーの接続先をそのまま使う前に、現在の音声PCへ到達できることを確認してください。

Node.js 22.22.0以上、npmを用意し、リポジトリルートから実行します。2026-10-25の利用者指定により、開発時は設定なしで **`http://192.168.100.113:8765`** を使います。

```bash
cd apps/hossy-web
npm ci
npm run dev -- --host 127.0.0.1
```

`http://127.0.0.1:4321/`を開きます。画面の開始前確認は同一originの`/api/v1/voice/status`を経由して指定PCへ届きます。音声PC自体の起動やIPの自動探索は行いません。同じLAN等から到達できる必要があります。

別の接続先を使う場合だけ、次のファイルを作ります。既存設定は上書きしません。

```bash
if [ ! -f .env.local ]; then cp .env.example .env.local; fi
```

Windows PowerShellの場合:

```powershell
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

`.env.local`の`VOICE_API_UPSTREAM`へ、Webを動かすPCから到達する音声サーバーのoriginを保存します。音声サーバーも同じPCで動く場合だけ、`VOICE_API_UPSTREAM=http://127.0.0.1:8765`に変更します。IPが変わればこの設定も更新してください。

優先順位は **プロセス環境の`VOICE_API_UPSTREAM` → このアプリの`.env.local` → 既定値`http://192.168.100.113:8765`**。ファイルやキーがない場合は既定値を使い、明示的な空文字は接続を無効化します。古い空欄テンプレートの`.env.local`を作成済みなら値を設定してください。意図しない環境変数はmacOS/Linuxの`unset VOICE_API_UPSTREAM`、PowerShellの`Remove-Item Env:VOICE_API_UPSTREAM -ErrorAction SilentlyContinue`で除いてから起動します。

設定変更後は開発サーバーを再起動してください。Windowsでは必要に応じて`npm.cmd`を使います。別clone/worktreeの私有設定は自動共有しません。devはCompose用`.env`を読みません。この既定値はdev専用で、公開CT/Composeは到達可能な接続先を明示設定します。CTからLAN内のIPへ経路がない問題は、この設定では解消しません。

`VOICE_API_UPSTREAM`には`http(s)://host[:port]`のoriginのみを指定します。**`/api/v1/voice/status`などのパスは付けません。** 末尾スラッシュ、資格情報、queryも起動時に拒否します。`.env.local`はGit/Dockerへのコピー対象外で、接続先をクライアントJSに埋め込みません。ブラウザーは同一originのAPIだけを呼びます。明示的な空設定は503 JSON、接続不能/タイムアウトは502/504 JSONで返し、開始失敗のまま理由を表示します。

```bash
npm run build
npm run preview -- --host 127.0.0.1
```

`preview`は静的画面の確認用で、接続先が設定済みでもAPIを転送しません。会話試験には`npm run dev`か、API転送を設定したNginx配信を使います。dev/build/previewの前に共有音声JSと2つのWorkletをpublicへコピーします。生成コピーとdistはGit対象外です。マイクにはHTTPSまたはlocalhostのセキュアコンテキストが必要です。

## 別PCで音声サーバーを動かす場合

`127.0.0.1`はコマンドを実行する機械自身です。WebがMac、音声が別PCなら、Webの転送先には音声PCのLANアドレスを指定します。`192.168.100.113`は2026-10-25に指定されたdev既定値です。実際のPCのIPが固定される保証はないので、ネットワーク変更後は再確認してください。

まず**音声PC側**で、サーバー自身の応答と待受を確認します（Ubuntu）。

```bash
curl -i --connect-timeout 3 --max-time 5 http://127.0.0.1:8765/api/v1/voice/status
ss -ltnp 'sport = :8765'
```

`127.0.0.1:8765`の待受はPC内部専用です。別PCからのアクセスが必要なら、担当者が実行中の会話と起動方法を確認し、既存プロセスを通常の方法で停止してからLANアドレスで起動します。手動起動はそのターミナルのCtrl+C、常駐サービスは既存の管理方法を使い、二重起動しません。既にLANアドレスだけで待受している場合、localhostへのcurlが失敗しても停止中とは限りません。

```bash
# 音声PCの検証対象リポジトリのルート。実際のLAN IPへ置き換える。
bash server/start_server_ubuntu.sh 192.168.100.113 8765
```

**Webを動かすMac側**から到達を確認します。接続できなければ、PCの現在のIP、待受、LAN経路/端末間通信制限、ファイアウォールを切り分けます。PCの音声/設定APIは、信頼する検証端末から到達する限定LANで使います。

```bash
curl -i --connect-timeout 3 --max-time 5 http://192.168.100.113:8765/api/v1/voice/status
```

200 JSONを確認後、Mac側の`apps/hossy-web`で起動します。既に起動中なら、転送先変更前にその開発サーバーを停止します。今回使用したAstro CLIはバックグラウンドで起動し、同ディレクトリの`./node_modules/.bin/astro dev status` / `./node_modules/.bin/astro dev stop`で確認/停止できました。前面実行の環境では起動ターミナルのCtrl+Cを使います。

```bash
# 既定の音声PCなら追加設定不要。変更時のみ.env.localで上書きする。
npm run dev -- --host 127.0.0.1
```

別ターミナルから製品Webと同じoriginで確認します。

```bash
curl -i --connect-timeout 3 --max-time 5 http://127.0.0.1:4321/api/v1/voice/status
```

2026-10-25 12:03 JST、この経路で200、`Content-Type: application/json`、`voice_api=available`、`Cache-Control: no-store`を確認しました。`models=checked_on_first_turn`の応答であり、モデル/実音声往復の合格ではありません。ブラウザーで`http://127.0.0.1:4321/`を再読込し、[通し試験](../demo.md#ローカル製品webからの通し試験)へ進みます。音声PCの稼働SHAは未取得です。

### 「音声APIからJSONが返りません」の切り分け

旧版の「JSONが返りません」はログイン問題とは限りません。修正版は未設定・到達不能・認証・経路・不正応答を区別し、開始を拒否します。上記のcurlでHTTP statusとContent-Typeを確認します。

| 応答 | 確認すること |
| --- | --- |
| 502/504 | 音声PCへの接続失敗/タイムアウト。現在のIP、音声サーバーの起動・LAN待受・経路を確認。修正版dev/Nginx自身の転送障害はno-store JSONで返す |
| 401/403、ログインHTML、認証へのredirect | 正規ログインと認証後のAPI経路。認証を外して解決しない |
| 404 | 静的preview、APIパス、配信設定を確認。会話試験はdevまたはAPI転送済み配信を使う |
| 503 JSON、voice_upstream_unconfigured | 接続先が空。devの.env.localまたは起動環境、Composeの環境設定を確認 |
| その他の503 JSON | 音声サーバー側のエラー。APIのmessageとPC側ログを確認。未設定と決めつけない |
| 200だがHTML/壊れたJSON/契約不一致 | 配信先・API版を確認。成功と扱わず、ログインHTMLとJSONの構文/内容不正を区別する |
| 200 JSON、voice_api=available | API到達は成功。モデル・マイク・音声往復は別途確認 |

CTは別ネットワークなので、このLAN IPを設定するだけでは接続できません。公開時はCT→音声PCのVPN等の経路と認証/HTTPSを整備します。ブラウザーから音声PCのLAN HTTPへ直接接続する構成にはせず、同一originのAPI転送を維持します。

## Docker / Nginx

Docker EngineとComposeプラグインを用意し、次を実行します。これは手順であり今回のDocker実行結果ではありません。

```bash
cd apps/hossy-web
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 web
```

ビルドcontextはリポジトリルート、Dockerfileは `apps/hossy-web/Dockerfile`。共有音声資産をコピーしてビルドし、Nginxで静的配信します。既定80番、Git対象外の `.env` の `HTTP_PORT` で変更できます。

音声転送には同ファイルまたは起動環境で `VOICE_API_UPSTREAM` を明示します。**Composeは`.env.local`を自動では読みません。** devのファイルを使う場合は、コンテナから到達できる値であることを確認し、`docker compose --env-file .env.local up -d --build`と明示します。開発時のlocalhost設定をそのまま流用しません。接続先はNginxコンテナから到達可能なoriginにし、コンテナの127.0.0.1はコンテナ自身であることに注意してください。未指定では音声/runtime経路に503 JSONを返します。許可する転送先パスは `/api/v1/voice/` 配下と完全一致の `/api/v1/dashboard/runtime` のみで、その他の `/api/` は404です。共通のdashboard settingsを個人画面の設定へ接続しません。

転送はバッファ・proxy cacheを無効にし、HTTPS upstreamの証明書を検証します。Nginx自身の転送502/504だけをJSON化し、上流APIが返した認証応答やエラー本文は書き換えません。公開時のPassportやTLS終端は別途管理する既存経路に従います。GitHubへのpushだけで公開サイトが更新されるわけではありません。

## PWAとキャッシュ

SWは画面・JS・CSS・画像・manifestだけを許可リストで保存し、API・認証・音声応答は保存しません。静的取得は4秒で打ち切り、通信失敗/5xx時は既存キャッシュを利用します。401/403、リダイレクト、アプリではないHTMLは保存せず、ホームの既存キャッシュも失効させます。オフラインの画面表示は音声会話のオフライン動作を意味しません。

実端末のPWAインストール/オフライン、実マイク、ESP32、Docker起動、公開デプロイは未検証です。URLのscheme/host/portを変えるとlocalStorageは別領域になります。元のサイトデータを消去せず確認してください。[結合テスト表](../demo.md#公開製品webの結合テスト準備)も参照します。
