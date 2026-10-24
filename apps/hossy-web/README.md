# ねんぎつね 製品Webアプリ / PWA — HOSSy

スマートフォン向けのホーム、ダイアリー、入眠準備タイマー、サウンド選択、製品紹介を備えるAstroアプリです。ソースはHOSSyの `107b762`（元プロジェクト `b300ac8`）を統合しています。

**音声会話・サウンド再生・ESP32接続は未実装です。** 現在の音声会話は別の [管理・音声Web](../web/README.md) を使います。mainへのコード統合と音声APIへの接続は別の作業です。

## 起動

Node.js 22.22.0以上。リポジトリのルートから実行します。

```bash
cd apps/hossy-web
npm ci
npm run dev -- --host 127.0.0.1
```

`http://127.0.0.1:4321/` を開きます。Windows PowerShellでは必要に応じて `npm.cmd` を使います。本番生成は `npm run build`、確認は `npm run preview -- --host 127.0.0.1`。

## 構成

- `src/pages/index.astro`：全画面の構造
- `src/layouts/Layout.astro`・`src/styles/app.css`：共通レイアウトとスタイル
- `public/app.js`：画面切替、タイマー、端末内保存、PWA案内
- `public/manifest.json`・`public/sw.js`・`public/icons/`：PWA
- `Dockerfile`・`compose.yaml`：静的ビルドとNginx配信

旧 `astro-site/` の中身をこのフォルダー直下へ移しました。画面コード・画像・保存キーは維持しています。

## 仕様と運用

- [画面・保存・未実装機能の仕様](../../docs/requirements/web-product.md)
- [ローカル起動・Docker・PWAの確認手順](../../docs/setup/hossy-web.md)
- [画面の処理フローと統合境界](../../docs/architecture.md)
- [移動前の開発メモと画像](../../docs/web/README.md)

既存のlocalStorageは配信元（scheme/host/port）に紐づきます。公開URLを変えると以前の記録が見えなくなります。ソースフォルダーの移動だけではブラウザーの記録を移行・削除しません。PWAのパスはサイトルート前提です。
