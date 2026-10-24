# 外部素材とライセンス

プロジェクトのMITライセンスは、次の第三者素材の個別ライセンスを置き換えません。2026-10-25のmain統合時に同梱ファイル・出典・ライセンス表示を確認しました。

## 心音素材

- 対象: `apps/web/sounds/heartbeat.ogg`
- 作品: Heartbeat.ogg / 作者: HerbertBoland
- [配布元](https://commons.wikimedia.org/wiki/File:Heartbeat.ogg) / [原掲載](https://www.freesound.org/people/HerbertBoland/sounds/32731/)
- ライセンス: [Creative Commons Attribution 3.0 Unported](https://creativecommons.org/licenses/by/3.0/)
- ファイル自体は未改変。クライアントで音量を調整・ループします。
- 配布元と一致するSHA-1: `929e11379465ea6c181f0e0a5956b7baa46b55bd`（199,816 bytes）。隣接のATTRIBUTION.txtも保持します。

## Gradle Wrapper

- 対象: `apps/android/gradlew`、`gradlew.bat`、`gradle/wrapper/gradle-wrapper.jar`
- Gradleの生成スクリプト・Wrapper。スクリプトの著作権表示とApache-2.0表記、JAR内のMETA-INF/LICENSEを保持します。
- JAR内のライセンス原文を [Gradle-Wrapper-LICENSE.txt](docs/licenses/Gradle-Wrapper-LICENSE.txt) にも同梱します。
- [Gradle公式ソース](https://github.com/gradle/gradle)。アプリの配布にはそのビルドに含める依存ライブラリの表示も別途必要です。

## Androidテンプレートのロボット図形

- 対象: `apps/android/app/src/main/res/drawable/ic_launcher_foreground.xml` と各密度の `mipmap-*/ic_launcher*.webp`。
- Android Studioテンプレートに由来するAndroid robot図形。白色・背景・影付きの派生表示です。
- [Googleの配布条件](https://developer.android.com/distribute/marketing-tools/brand-guidelines#android_robot) / [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)
- The Android robot is reproduced or modified from work created and shared by Google and used according to terms described in the Creative Commons 3.0 Attribution License.
- Android is a trademark of Google LLC. Googleによる製品の推奨を意味しません。

## 同梱しないもの

モデル重み、Irodori等の外部ランタイム本体、ユーザーの参照音声・録音・生成音声、実Wi-Fi設定・.env、APKなどのビルド成果物は公開対象にしません。モデルや依存パッケージは各配布元の条件を確認して別途取得します。新しい音声・画像・コードを追加する場合は、再配布条件と出典を確認し、この一覧へ追記してください。
