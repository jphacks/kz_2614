# ねんぎつね 製品Webアプリ / PWA — HOSSy

スマートフォン向けのホーム、ダイアリー、入眠準備タイマー、サウンド選択、製品紹介を備えるAstroアプリです。OneDevの `JPHACKS2026_app` main `caf3541`（2026-10-25取得）を同期しています。夜空テーマ、ローディング画面、新しいロゴ、時間選択ボタンを含みます。

公開製品Web: [ホーム](https://hiroron-jphacks2026.hiroron.net/) / [ダイアリー（#diary）](https://hiroron-jphacks2026.hiroron.net/#diary)。2026-10-25の未ログイン確認ではhiroron Passportのログイン画面が表示され、ログイン後の本文・音声接続・公開版とこのソースの一致は未確認です。

製品画面から同一originの音声APIへ開始・連続PCM入力・途中文字・返答音声・停止を接続するコードを実装しています。[管理・音声Web](../web/README.md) の共有クライアントを利用します。実マイク・公開環境の音声往復は未検証です。サウンド選択は音源再生に接続せず、人形所有権・BLE・接触センサー・AI/他者モードの実APIも未実装です。

## 起動

Node.js 22.22.0以上。リポジトリのルートから実行します。

```bash
cd apps/hossy-web
npm ci
npm run dev -- --host 127.0.0.1
```

設定なしでも`npm run dev`で指定の音声PC `http://192.168.100.113:8765`へ自動転送します。変更する場合だけ`.env.example`からGit対象外の`.env.local`を作り、`VOICE_API_UPSTREAM`へ別のoriginを保存してください。起動環境 → `.env.local` → 既定の音声PCの順で優先し、明示的な空文字は接続を無効化します。接続不能・認証・不正応答は開始失敗として表示します。この既定値は開発用で、公開CTの設定は別です。[設定手順](../../docs/setup/hossy-web.md)を参照してください。`http://127.0.0.1:4321/` を開きます。Windows PowerShellでは必要に応じて `npm.cmd` を使います。本番生成は `npm run build`、画面だけの確認は `npm run preview -- --host 127.0.0.1`。previewはAPIを転送しないので、会話試験にはdevを使います。

## 構成

- `src/pages/index.astro`：全画面の構造
- `src/layouts/Layout.astro`・`src/styles/app.css`：共通レイアウトとスタイル
- `public/app.js`：画面切替、音声開始/停止フック、会話期限、端末内保存、PWA案内
- `scripts/sync-voice-assets.mjs`：dev/build/previewの前に共有音声JSとWorkletを `apps/web` からコピー。生成コピーを直接編集しない
- `public/manifest.json`・`public/sw.js`・`public/icons/`：PWA
- `Dockerfile`・`compose.yaml`：静的ビルドとNginx配信

OneDevの `astro-site/` の中身をこのフォルダー直下へ対応させています。画面コード・画像・保存キーは元ソースに合わせ、Composeのビルドcontextは共有音声ソースを含めるためリポジトリルート `../..` です。共有リポジトリ用の除外設定を保持しています。元の開発メモは `docs/web/history/hossy/`、素材原本は `images/` に配置します。

## 仕様と運用

公開環境の確認は [ログインから連続会話・停止までの結合テスト表](../../docs/demo.md#公開製品webの結合テスト準備) を使います。公開HTTPSからPCのローカルHTTPへ直接つなぐ前提にせず、既存の配信・認証・API到達経路を先に確認してください。

- [使い方：タイマー・ダイアリー・保存・PWA](../../docs/web/usage.md)
- [画面・保存・未実装機能の仕様](../../docs/requirements/web-product.md)
- [ローカル起動・Docker・PWAの確認手順](../../docs/setup/hossy-web.md)
- [画面の処理フローと統合境界](../../docs/architecture.md)
- [移動前の開発メモと画像](../../docs/web/README.md)

既存のlocalStorageは配信元（scheme/host/port）に紐づきます。公開URLを変えると以前の記録が見えなくなります。ソースフォルダーの移動だけではブラウザーの記録を移行・削除しません。PWAのパスはサイトルート前提です。
