# v0.2.0 — 配布版 / Distribution release

- モデルの順次読み込みを標準化。低速ストレージでの同時読み出しの競合を抑えます。明示設定 HF_DEACTIVATE_ASYNC_LOAD=0 で並列読み込みに戻せます。ストレージによって効果は異なり、推論ステップ自体の高速化を保証しません。
- キュー更新は全待機・実行ジョブと直近8完了ジョブを保持し、各ジョブの結果転送を16件へ制限。個別ジョブAPIと保存画像は省略しません。
- ブラウザーの結果保持を16件へ制限し、一度の更新での重複描画を削減。過去画像は履歴と出力フォルダから利用できます。
- 履歴の最新件数選択を省メモリ化。壊れたJSONや走査中のファイル消失で一覧全体が失敗しないよう修正。
- 鳩のプリセット3種類と実生成サンプル、利用責任・サポート方針を同梱。
- モデル・環境・ログ・個人データは同梱せず、サンプル設定から環境情報を除外。

English: Sequential model loading by default, bounded queue response and browser previews, resilient history handling, three illustrated pigeon presets, and bilingual usage/support policy. Sequential loading reduces competing reads on slow storage; it is not a promised inference speedup.

検証範囲 / Validation scope: automated regression checks cover API, queue, history, settings, paths and process lifetime. Pigeon samples were generated with the real model. Generated composition is not guaranteed to match every prompt detail. Presets for other GPU capacities do not constitute individual hardware certification. Machine-specific logs and performance reports are intentionally excluded.
