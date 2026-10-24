# 3Dプリント部品

`namikawa_3Dprint` の `7c3e9e91e940e471b2aff0de08ca4b2a8c760050` から取り込んだSTL 8点です。元のファイル名と内容を保持しています。担当者名は未指定で、ブランチ名から氏名を推測しません。

## ファイル一覧

用途の説明はファイル名から読み取れる範囲です。設計図・組立図・実機照合による用途の確定ではありません。

| ファイル | 用途・確認範囲 |
| --- | --- |
| [battery_case_v2.stl](battery_case_v2.stl) | 電池ケースを示す名称。電池の種類・本数・寸法は未確認 |
| [case_base.stl](case_base.stl) | ケース底部を示す名称。対象部品・他のケースとの関係は未確認 |
| [case_lid.stl](case_lid.stl) | ケース蓋を示す名称。対象部品・底部との組合せは未確認 |
| [esp32_case_base_v4.stl](esp32_case_base_v4.stl) | ESP32ケース底部を示す名称。対応する基板の型式・寸法は未確認 |
| [esp32_case_lid_v4.stl](esp32_case_lid_v4.stl) | ESP32ケース蓋を示す名称。底部との嵌合は未確認 |
| [sg90_arm_round_tip.stl](sg90_arm_round_tip.stl) | SG90アームの丸い先端部を示す名称。取付方法・可動域は未確認 |
| [sg90_arm_servo_mount.stl](sg90_arm_servo_mount.stl) | SG90アームのサーボ取付部を示す名称。穴位置・固定方法は未確認 |
| [sg90_pole_mount_edge_v3.stl](sg90_pole_mount_edge_v3.stl) | SG90のポール/端部マウントを示す名称。対象形状・組付け方向は未確認 |

## 印刷・組立前の確認

寸法・単位・スケール、材料、積層ピッチ、充填率、造形方向、サポート等の印刷条件は未確認です。この取り込みでは印刷・組立・実機との嵌合確認を行っておらず、過去の印刷実績も確認していません。名称に含まれる `v2`・`v3`・`v4` はファイル名の一部で、検証済みの版を意味しません。

スライサーで寸法と造形条件を確認し、使用する基板・電池・サーボの実寸、固定穴、配線の逃げ、可動部の干渉を確認してから使用してください。印刷・嵌合を確認した際は、機材・条件・対象ファイル・結果を [開発履歴](../docs/development/work-history.md) に残します。

関連資料: [ESP32機器仕様](../docs/requirements/esp32.md) / [フォルダー構成](../docs/repository-layout.md) / [担当範囲](../docs/development/ownership.md) / [ブランチ統合記録](../docs/development/branches.md)
