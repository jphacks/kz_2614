# 製品Webアプリの起動・配信

対象は [apps/hossy-web](../../apps/hossy-web/README.md)。8765番の音声管理Webとは別の入口で、同じ共有音声クライアントを使います。[仕様](../requirements/web-product.md) / [使い方](../web/usage.md)。

## 公開先と確認状況

[公開ホーム](https://hiroron-jphacks2026.hiroron.net/) / [ダイアリー](https://hiroron-jphacks2026.hiroron.net/#diary)。公開URLは利用者指定です。未ログインの `GET /api/v1/voice/status` は401・text/htmlで、Passport認証後のUI・API転送先・実際の配信版は未確認です。ログイン画面への到達を音声API成功とは扱いません。

ブラウザーは同一originの `/api/v1/voice/` と `/api/v1/dashboard/runtime` にアクセスします。配信サーバー側からPC音声サーバーへ転送する経路を担当者と確認してください。公開HTTPSからブラウザーがPCのHTTPへ直接接続する設定にはしません。音声サービスはPCのHTTP 8765番と定義済みで、公開配信側の具体的なupstream設定と到達性は未確認です。この変更は公開サイトへのデプロイ・認証設定・サーバー再起動を含みません。

## ローカル開発

Node.js 22.22.0以上、npmを用意し、リポジトリルートから実行します。

```bash
cd apps/hossy-web
npm ci
npm run dev -- --host 127.0.0.1
```

`http://127.0.0.1:4321/` を開きます。Windowsでは必要に応じ `npm.cmd` へ読み替えます。音声APIを使う検証では、担当者が確認した接続先を起動前に指定します。次の値は同一PCに実際のサーバーがある場合だけの例です。

```bash
VOICE_API_UPSTREAM=http://127.0.0.1:8765 npm run dev -- --host 127.0.0.1
```

`VOICE_API_UPSTREAM` は `http(s)://host[:port]` のoriginのみ。パス、末尾スラッシュ、資格情報、queryは指定できません。devの転送はopt-inで、未指定なら音声APIのルートはなく404等になります。サーバーを自動起動しません。マイク利用はHTTPSまたはlocalhostのセキュアコンテキストが必要です。

```bash
npm run build
npm run preview -- --host 127.0.0.1
```

`preview` は静的ビルド確認用でAPIプロキシを提供しません。dev/build/previewの前に `scripts/sync-voice-assets.mjs` が `apps/web` の音声JSと2つのWorkletをpublicへコピーします。生成コピーとdistはGit対象外です。

## 別PCで音声サーバーを動かす場合

`127.0.0.1`はコマンドを実行する機械自身です。WebがMac、音声が別PCなら、Webの転送先には音声PCのLANアドレスを指定します。以下の`192.168.100.113`は2026-10-25の確認値であり、固定値ではありません。ネットワーク変更後は再確認してください。

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
VOICE_API_UPSTREAM=http://192.168.100.113:8765 npm run dev -- --host 127.0.0.1
```

別ターミナルから製品Webと同じoriginで確認します。

```bash
curl -i --connect-timeout 3 --max-time 5 http://127.0.0.1:4321/api/v1/voice/status
```

2026-10-25 12:03 JST、この経路で200、`Content-Type: application/json`、`voice_api=available`、`Cache-Control: no-store`を確認しました。`models=checked_on_first_turn`の応答であり、モデル/実音声往復の合格ではありません。ブラウザーで`http://127.0.0.1:4321/`を再読込し、[通し試験](../demo.md#ローカル製品webからの通し試験)へ進みます。音声PCの稼働SHAは未取得です。

### 「音声APIからJSONが返りません」の切り分け

画面の案内だけではログイン問題とは断定できません。上記のcurlでHTTP statusとContent-Typeを確認します。

| 応答 | 確認すること |
| --- | --- |
| 502、text/plainや空本文 | 転送先に接続できない等のproxy障害。今回の原因はWebの転送先がMac自身だったこと。音声PCの待受もLAN向けへ変更した |
| 401/403、ログインHTML、認証へのredirect | 正規ログインと認証後のAPI経路。認証を外して解決しない |
| 404 | devのupstream未指定、静的preview、パス/配信設定を確認 |
| 503 JSON | Nginxのupstream未設定等。JSON内のエラーと配信設定を確認 |
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

音声転送には同ファイルまたは起動環境で `VOICE_API_UPSTREAM` を明示します。接続先はNginxコンテナから到達可能なoriginにし、コンテナの127.0.0.1はコンテナ自身であることに注意してください。未指定では音声/runtime経路に503 JSONを返します。許可する転送先パスは `/api/v1/voice/` 配下と完全一致の `/api/v1/dashboard/runtime` のみで、その他の `/api/` は404です。共通のdashboard settingsを個人画面の設定へ接続しません。

転送はバッファ・proxy cacheを無効にし、HTTPS upstreamの証明書を検証します。公開時のPassportやTLS終端は別途管理する既存経路に従います。GitHubへのpushだけで公開サイトが更新されるわけではありません。

## PWAとキャッシュ

SWは画面・JS・CSS・画像・manifestだけを許可リストで保存し、API・認証・音声応答は保存しません。静的取得は4秒で打ち切り、通信失敗/5xx時は既存キャッシュを利用します。401/403、リダイレクト、アプリではないHTMLは保存せず、ホームの既存キャッシュも失効させます。オフラインの画面表示は音声会話のオフライン動作を意味しません。

実端末のPWAインストール/オフライン、実マイク、ESP32、Docker起動、公開デプロイは未検証です。URLのscheme/host/portを変えるとlocalStorageは別領域になります。元のサイトデータを消去せず確認してください。[結合テスト表](../demo.md#公開製品webの結合テスト準備)も参照します。
