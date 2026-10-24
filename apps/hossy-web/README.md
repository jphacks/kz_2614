# ねんぎつね 製品Webアプリ / PWA — HOSSy

スマートフォン向けのホーム、ダイアリー、入眠準備タイマー、サウンド選択、製品紹介を備えるAstroアプリです。OneDevの `JPHACKS2026_app` main `caf3541`（2026-10-25取得）を同期しています。夜空テーマ、ローディング画面、新しいロゴ、時間選択ボタンを含みます。

公開製品Web: [ホーム](https://hiroron-jphacks2026.hiroron.net/) / [ダイアリー（#diary）](https://hiroron-jphacks2026.hiroron.net/#diary)。2026-10-25の未ログイン確認ではhiroron Passportのログイン画面が表示され、ログイン後の本文・音声接続・公開版とこのソースの一致は未確認です。

**このリポジトリの製品Webでは音声会話・サウンド再生・ESP32接続は未実装です。** 現在の音声会話は別の [管理・音声Web](../web/README.md) を使います。mainへのコード統合と音声APIへの接続は別の作業です。

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

OneDevの `astro-site/` の中身をこのフォルダー直下へ対応させています。画面コード・画像・保存キーは元ソースに合わせ、Composeのビルド先 `.` と共有リポジトリ用の除外設定を保持しています。元の開発メモは `docs/web/history/hossy/`、素材原本は `images/` に配置します。

## 仕様と運用

公開環境の確認は [ログインから連続会話・停止までの結合テスト表](../../docs/demo.md#公開製品webの結合テスト準備) を使います。公開HTTPSからPCのローカルHTTPへ直接つなぐ前提にせず、既存の配信・認証・API到達経路を先に確認してください。

- [使い方：タイマー・ダイアリー・保存・PWA](../../docs/web/usage.md)
- [画面・保存・未実装機能の仕様](../../docs/requirements/web-product.md)
- [ローカル起動・Docker・PWAの確認手順](../../docs/setup/hossy-web.md)
- [画面の処理フローと統合境界](../../docs/architecture.md)
- [移動前の開発メモと画像](../../docs/web/README.md)

既存のlocalStorageは配信元（scheme/host/port）に紐づきます。公開URLを変えると以前の記録が見えなくなります。ソースフォルダーの移動だけではブラウザーの記録を移行・削除しません。PWAのパスはサイトルート前提です。
