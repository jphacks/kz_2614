# GitHub向けのHOSSY＿WEB取り込み

記録日時：2026-10-25 06:46。

利用者の確認により、対象は`C:\〇Codex-JPHACK2026app`。共有GitHubリポジトリ`jphacks/kz_2614`の`HOSSY＿WEB`ブランチ（全角＿）へ追加する。

配置は`apps/hossy-web/`。元の追跡49ファイルをそのままコピーし、SHA-256一致をコピー後とビルド後に確認した。既存の音声Web、サーバー、ESP32を保持し、製品UIは独立して起動する。

確認結果：npm ci成功、Astro 7.4.1の本番ビルド成功、app.jsとsw.jsのNode構文成功。生成物の10 HTTPパスが200で、manifest・Service Worker・アイコン・CSS・画像の配信を確認した。元49ファイルすべてがGit追加対象であることも確認済み。

起動方法・Dockerの手順・未統合範囲は[プロジェクトREADME](../README.md)へ記載した。Dockerのビルドや公開サイトへの反映、ブラウザーによる動作・PWAインストールは今回の確認範囲に含めない。
