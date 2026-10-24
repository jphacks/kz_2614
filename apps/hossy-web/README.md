# HOSSyのねんぎつねWeb UI

`C:\〇Codex-JPHACK2026app`にある独立したAstroプロジェクトを、このリポジトリへ追加したものです。元の`astro-site/`、Compose、開発メモ、画像を保持しています。取り込み元のコミットは`b300ac8`です。

スマートフォン向けのホーム、ダイアリー、入眠準備タイマー、サウンド選択、製品紹介、PWAのUIプロトタイプです。選択やタイマー記録はブラウザーのlocalStorageへ保存します。睡眠グラフはサンプルで、音声会話・音声再生・ESP32との接続は未実装です。

## ローカル起動

リポジトリのルートから実行します。Node.js 22.22.0以上が必要です。

```powershell
cd apps/hossy-web/astro-site
npm.cmd ci
npm.cmd run dev
```

`http://localhost:4321/`を開きます。Linux/macOSでは`npm.cmd`を`npm`へ読み替えてください。

```powershell
npm.cmd run build
npm.cmd run preview
```

## Dockerで配信

```sh
cd apps/hossy-web
docker compose up -d --build
```

標準はHTTPポート80です。必要ならこのディレクトリのGit対象外`.env`へ`HTTP_PORT=8080`等を設定します。Docker・公開ドメインへの反映は別途実行してください。GitHubへのpushだけでは稼働サイトは更新されません。

元の[DEPLOY.md](DEPLOY.md)は、独立したOneDevリポジトリで運用していた際の手順・ドメインの記録です。このGitHubリポジトリで使う場合は上記の作業ディレクトリを起点にします。

## 他のWeb画面との関係

[apps/web](../web/README.md)はPC音声サーバーに接続された既存の操作画面です。このAstroプロジェクトは別のアプリとして追加しています。APIへの接続、画面統合、サーバーの配信先変更は今後の作業です。

このアプリは`/app.js`・`/sw.js`・PWAのscope等がサイトのルートを前提とします。サブディレクトリへ配信する場合はパス・Service Worker・manifestの調整が必要です。公開環境でのPWA利用にはHTTPSが必要です。

## 元の資料

- [画面と機能の説明](astro-site/README.md)
- [開発記録](Codex-memo/2026-10-24_1527_おやすみカード全体の操作.md)
- [共有リポジトリの構成](../../docs/repository-layout.md)

`node_modules/`、`dist/`、`.astro/`、npmキャッシュ、`.env`、元リポジトリの`.git/`は取り込みません。
