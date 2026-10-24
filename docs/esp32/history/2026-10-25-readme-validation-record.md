> 履歴資料：このファイルは当時の判断・実験記録です。現在の配置・API・MCP接続状態は[現行仕様の入口](../README.md)を参照してください。

# READMEから分離した実機検証記録

2026-10-25、統合版975b3adの機器READMEから履歴を保存した。以下は当時の記録で、現在のIP・COM・モードを再確認した結果ではない。

## 現在の検証状態

2026-10-24、接続中の 2 台はいずれも **ESP32-D0WD-V3** と識別しました。Arduino CLI と Espressif のボードパッケージ 3.3.12 で両スケッチのビルドが成功し、**COM6 に Reader、COM7 に Follower** を書き込みました。両方ともフラッシュ内容のハッシュ照合が成功しています。

アクセスポイント有効化後、両方のシリアル出力で Wi-Fi 接続と IP 取得を確認しました。確認時の IP は **Reader: 192.168.100.112、Follower: 192.168.100.111** です。Reader は mDNS で Follower を発見し、`POST /api/teleop` への **HTTP 200** 応答を受け取りました。このときの指令角度は 0° でした。Reader は初回の成功応答をシリアルに一度表示します。

PC は ESP32 と別のネットワークに接続されているため、PC から Follower の `GET /api/status`、`POST /api/angle`、`POST /api/mode` へ直接アクセスする試験は未実施です。API を試す端末を ESP32 と同じ LAN に接続してください。IP は DHCP により変わる可能性があります。可変抵抗器を回したときのADC生値は0～4095まで変化しました。ユーザーの観察ではSG90の軸は約90°動いていますが、軸の角度をPCから実測したものではありません。

識別用 LED の追加後も両スケッチのビルドと書き込み・フラッシュ照合が成功しました。起動ログは COM6 が `Reader (sender); LED: one blink`、COM7 が `Follower (receiver); LED: two blinks` と表示し、Reader から Follower へのテレオペ HTTP 200 応答も再確認しました。LED の実際の発光は PC からは観察できていません。

Readerの校正機能を追加してCOM6へ再書き込みしました。シリアルから校正を実行し、ADC最小値0・最大値4095の保存成功応答を確認しました。再起動後に同じ範囲の表示、Wi-Fi再接続、テレオペ HTTP 200 応答を確認しています。保存した値が既定値と同じため、再起動時に不揮発メモリから読み込まれたかは独立には判別できていません。ユーザーはSG90の赤線をESP32の5 V/VINから外してアルカリ乾電池4本の別電源に接続し、動作中も約5.5 Vと報告しています。この配線と電圧はPCからは直接検証できていません。

Followerへパルス幅調整機能を追加してCOM7へ再書き込みし、フラッシュのハッシュ照合に成功しました。シリアルの `v` コマンドで0°→1000 µs、150°→2000 µsと表示されました。実機を見ながら一時的に下限950 µs、上限2050 µs、さらに上限2100 µsを試しましたが、ユーザーの観察では旋回は約90°のままで、うなりはありませんでした。調整値は保存せず `r` で1000～2000 µsへ戻し、シリアルでも復帰を確認しました。購入品は[Amazon ASIN B0B2QW7WGY](https://www.amazon.co.jp/dp/B0B2QW7WGY)のYFFSFDC製SG90 4個セットで、ユーザーが提示した商品説明は最大角度180°、動作電圧4.8～6 Vとしています。購入品の制御パルス幅はその説明に記載されておらず、他社製SG90の仕様をそのまま適用できません。実際の軸角・PWM波形・機械的な限界はPCから直接測定できていません。

別の1個に差し替えてもユーザーの観察では約90°・異音なしでした。Followerの診断を追加して再書き込み・フラッシュ照合を行い、可変抵抗器の両端でLEDC設定が50 Hz、デューティが819/1638、GPIO18のHIGH時間が約1001/2001 µsであることをシリアルから確認しました。これはESP32のGPIO18端子の入力回路による読み取りであり、サーボのコネクタでの実波形測定ではありません。Readerの指令とFollower端子のPWM幅は両端まで変わっているため、残る候補はサーボ個体の仕様・可動限界、または信号線・電源のサーボ端子側です。

その後、Followerのパルス幅を段階的に750～2250 µsへ広げました。ユーザーの観察では軸の可動域が約45°～135°から約20°～160°へ広がり、うなり・突き当たり・発熱はありませんでした。この範囲をFollowerの不揮発メモリへ保存し、再書き込み後もシリアルで750～2250 µsが読み込まれ、下限でGPIO18のHIGH時間が約749 µsであることを確認しました。スケッチの調整可能限界は500～2500 µsに拡張して書き込み済みです。続いて下限600 µs・上限2400 µsへ片側ずつ50 µs刻みで広げ、GPIO18のHIGH時間が両端で約601/2403 µsであることを確認しました。ユーザーの観察では軸は約5°～175°まで動き、うなり・突き当たり・発熱はありませんでした。この範囲をFollowerの不揮発メモリへ保存しました。Reader/Followerとも0～180°指令版をビルド・書き込みし、両方のフラッシュ照合に成功しました。Follower再起動後も保存値600～2400 µsが読み込まれ、Reader側はADC範囲0～4095をシリアルで確認しました。つまみの両端でFollowerは0°/600 µsと180°/2400 µsを受信し、GPIO18のHIGH時間は約601/2404 µsでした。ユーザーの最終確認でもSG90は反対端まで動き、異音や突き当たりはありませんでした。APIのPCからの直接試験は上記のネットワーク条件のため未実施です。

同日、SG90を合計3台に増やすためFollowerの出力をGPIO18・19・21に拡張し、起動時のモードを`neutral`に変更しました。確認時のCOM番号はFollowerがCOM6、ReaderがCOM9です。FollowerをビルドしてCOM6へ書き込み、フラッシュのハッシュ照合に成功しました。`v`のGPIO入力読み取りでは3ピンのHIGH時間が順に約1502・1502・1502 µsで、Readerが動作している間も1台目の指令は90°/1500 µsのままでした。`n`コマンドでも中立モードと3ピンの1500 µs出力を再確認しました。その後ユーザーが追加の2台を接続し、**3台とも実物で中立位置になった**と報告しました。正確な軸角度、3台接続時の電源電圧・電流は測定していません。

同日、3台を同一角度で動かすため、Readerの0～180°指令を各関節の約60°変化へ分配しました。FollowerをCOM6へ書き込み、起動時の中立1500 µs、テレオペ開始後のReader指令132°で3ピンとも約1641 µs、0°で約1201 µsを確認しました。ユーザーは180°側でリンクが伸び、異常がないと報告しました。その後、Followerが再起動後に自動でテレオペを開始するよう変更し、0°側をより丸くする調整コマンド`5`/`6`を追加しました。再ビルド・COM6への書き込み・フラッシュ照合が成功し、操作コマンドなしで180°指令時に3ピンとも約1802 µsを確認しました。0°側ではユーザーの観察を挟んで50 µsずつ2回、1200→1150→1100 µsへ調整し、3ピンとも約1101～1102 µsを確認しました。ユーザーは各段階でリンクがより丸くなり、異音・突き当たり・発熱・接触がないと報告しました。計100 µsの調整をFollowerの不揮発メモリへ保存し、再起動後も`curl extra: 100 us`、0°指令・1100 µs、3ピンの約1101～1102 µsを確認しました。PCが測ったのはGPIOパッドのHIGH時間であり、リンクの実角度と3台接続時の電源電圧は未測定です。

同日、Followerに`POST /api/konkon`を追加し、開始角度・終了角度・速度を指定した非ブロッキングの往復制御を実装しました。ESP32 core 3.3.12でビルドし、Followerへ書き込み・フラッシュ照合に成功しました。試験時はUSB差し替えでCOM番号が変わり、MACアドレスでReaderがCOM6、FollowerがCOM11と確認しました。PCはアクセスポイントと別ネットワークなので、Readerに一時的なHTTP試験プログラムを入れて同じWi-FiからAPIを呼びました。`GET /api/status`はHTTP 200、`POST /api/angle`の90°指令は`mode=api`で1500 µsを保持、`POST /api/konkon`の`start_angle=60&end_angle=90&speed=15`はHTTP 200で`mode=konkon`になりました。続く状態取得では角度が90→74→85→67°と変化し、折り返し目標が60°から90°へ変わることを確認しました。往復中の`POST /api/angle`で75°へ停止し、`mode=teleop`への復帰もHTTP 200で確認しました。ユーザーの観察では、この設定は動きの方向は合っていましたが、幅が小さく速度も遅かったため、`start_angle=0&end_angle=180&speed=60`へ変更して約20秒間再試験しました。HTTP 200で往復モードへ移り、状態APIの指令角度と折り返し目標が変化することを確認しました。ユーザーはこの設定の幅・速さが良く、異音・突き当たり・発熱・リンク同士の接触もないと確認しました。試験後Readerへ元のスケッチを書き戻してフラッシュ照合し、Followerが可変抵抗器の0°指令を再び受け、3ピンで約1101～1102 µsを出すことを確認しました。PCからのHTTP直接アクセス、実際のリンク角度、速度の実測、Wi-Fi切断時の停止動作は未検証です。

今回のビルドでは、元の作業パスに含まれる `〇` を ESP32 リンカが処理できず、`X:` のドライブ別名から同じファイルを参照しました。CLI とボード環境は作業フォルダの `.tools` と `.arduino-data` に保存しています。`X:` の別名はコマンドを実行するセッションごとに作成が必要です。ビルド・書き込みログは `.build` にあります。

同日、Followerに3台共通の`relax`モードを追加しました。ESP32 core 3.3.12でビルドし、Follower（MAC末尾40:18、COM11）へ書き込み・フラッシュ照合に成功しました。シリアルの`d`で停止させる前は3ピンとも約1100 µs、停止後の`v`ではLEDCデューティ0、GPIO18・19・21のHIGH検出がすべてタイムアウトでした。ユーザーはリンクの保持力が弱まったと確認しました。同じWi-Fi上のReaderへ一時試験プログラムを入れ、`GET /api/status`で`mode=relax`と`pwm_enabled=false`、脱力中の`POST /api/angle`でHTTP 409、`POST /api/mode`の`mode=neutral`・`mode=relax`・`mode=teleop`でそれぞれHTTP 200を確認しました。復帰後は3ピンとも約1502 µsを出しました。Reader（MAC末尾02:60、COM6）には元の可変抵抗器用スケッチを書き戻して照合し、FollowerがReaderから0°を受け、3ピンとも約1101 µsを出すことを確認しました。PWM停止中もサーボ電源は接続されたままで、停止時の消費電流は測定していません。

## 参考資料

- [Espressif: ESP32 GPIO と ADC2/Wi-Fi の制約](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/gpio.html)
- [Espressif: Arduino-ESP32 ADC API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/adc.html)
- [Espressif: Arduino-ESP32 Preferences API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/preferences.html)
- [Espressif: ESP32 GPIOの許容電圧](https://docs.espressif.com/projects/esp-faq/en/latest/esp-faq-en-master.pdf)
- [Espressif: Arduino-ESP32 LEDC API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/ledc.html)
- [Pololu: サーボへの指令パルス停止時の挙動](https://www.pololu.com/blog/17/servo-control-interface-in-detail)
- [Tower Pro: SG90 の電源仕様](https://towerpro.com.tw/product/sg90-7/)
- [YFFSFDC製4個セットの販売情報](https://item.rakuten.co.jp/trenditemshop/b0b2qw7wgy/)
- [M5Stack製SG90: 180°で1～2 ms](https://docs.m5stack.com/en/accessory/SG90_Servo)
- [Keyestudio製マイクロサーボ: 180°で0.5～2.5 ms](https://wiki.keyestudio.com/Ks0194_keyestudio_Micro_Servo)
- [Adafruit: サーボのパルス幅を徐々に調整する注意](https://learn.adafruit.com/adafruit-crickit-creative-robotic-interactive-construction-kit/arduino-servos-2)
- [Panasonic: ニッケル水素充電池の電圧](https://www.panasonic.com/global/energy/products/eneloop/en/faq.html)
- [Energizer: アルカリ乾電池の新品電圧](https://data.energizer.com/pdfs/alkaline_appman.pdf)
