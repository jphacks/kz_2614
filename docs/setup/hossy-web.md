# 製品Webアプリの起動・配信

対象は [apps/hossy-web](../../apps/hossy-web/README.md)。既存の音声管理Web（8765番）とは別です。画面機能と未実装範囲は [製品Web仕様](../requirements/web-product.md) を確認してください。

## ローカル確認

Node.js 22.22.0以上、npmを用意し、リポジトリのルートから実行します。

```bash
cd apps/hossy-web
npm ci
npm run dev -- --host 127.0.0.1
```

`http://127.0.0.1:4321/` を開く。Windowsでは `npm.cmd` へ読み替え可能です。本番ビルドは次のとおりです。

```bash
npm run build
npm run preview -- --host 127.0.0.1
```

`dist/` は生成物としてGit除外します。previewはローカル確認用です。停止はそのターミナルでCtrl+C。音声サーバーの再起動は不要です。

## Docker

Docker EngineとComposeプラグインがある環境で、リポジトリのルートから実行します。

```bash
cd apps/hossy-web
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 web
```

Composeは同じフォルダーのDockerfileを使い、Nodeで生成したdistをNginxへ配置します。既定HTTP 80番。変更する場合はこのフォルダーのGit対象外 `.env` に `HTTP_PORT=8080` などを設定します。

更新時は未コミット変更を確認して最新mainを取得し、同じ場所で再ビルドします。GitHubへのpushだけでは既存公開サイトは変わりません。旧OneDev環境の履歴は [旧配信記録](../web/history/standalone-deploy.md) として残しています。

## PWA・スマートフォン

- スマホからは配信PCの実際のLANアドレスまたは配信ドメインを使います。スマホの127.0.0.1はスマホ自身です。
- 公開時はHTTPSと証明書を用意し、サイトルートから配信します。localhostはPC内の検証用です。
- `/manifest.json`、`/sw.js`、`/icons/nengitsune-192.png`、`/icons/nengitsune-512.png` とホームが200で返ることを確認します。
- Androidはブラウザーのインストール、iPhoneはSafariの共有メニューからホーム画面追加を確認します。
- URLのscheme/host/portを変えるとlocalStorageは別領域になります。元の保存先を消去しないで確認してください。

現在の製品Webに音声会話・音声再生・実機接続はありません。実際の音声往復はPCの `http://127.0.0.1:8765/#voice-call` で試します。
