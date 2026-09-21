# cvELD Image Studio v0.2.0

Qwen-Image-2.1向けのローカル画像生成WebUI。Windows 11 x64・NVIDIA CUDA GPUを対象に、テキスト生成、参照画像による編集、マスク指定の部分編集を行えます。 / A local Windows image-generation and editing UI for Qwen-Image-2.1.

## 起動 / Start

1. リリースZIPをすべてローカルSSDへ展開します。 / Extract the entire release ZIP to a local SSD.
2. `USAGE_AND_SUPPORT.md` と `docs/Manual_ja.html` を確認します。 / Read the usage policy and manual.
3. `Run.bat` を起動します。初回の利用条件確認は y/n、一度同意すると次回から省略します。専用Python・環境・モデルを取得して起動します。 / Run `Run.bat`; first-run consent is y/n, then setup downloads the dedicated environment and model.
4. DOS窓は開いたままにしてください。この窓を閉じるとツールも終了します。 / Keep the console open; closing it stops the application.

## 主な機能 / Features

- テキスト生成、最大10枚の参照画像による編集、ブラシによる部分編集 / Text generation, up to 10 reference images and region-guided editing.
- 生成回数1～99 × 同時枚数1～10、キュー・停止・履歴 / 1–99 batches × 1–10 images, queue, cancellation and history.
- 幅・高さの交換、ステップ・CFGスライダー、5種類の目的別設定、3種類のEulerスケジュール / Size swap, sliders, five generation presets and three Euler schedules.
- 鳩のサンプル3種類と画像付きマニュアル / Three pigeon prompts with illustrated documentation.
- `t2i`、`i2i`、`fix2i` に出力を分類 / Separate output folders.
- GPU容量別プリセット。低VRAM設定は速度と交換条件があり、全GPU・全サイズでの動作保証ではありません。 / VRAM presets have speed tradeoffs and are not universal compatibility guarantees.

## 配布内容 / Distribution

モデル重み、Python、依存パッケージのバイナリ、個人の生成履歴、ログ、ローカル環境情報は同梱しません。初回はインターネット接続と大きな空き容量が必要です。RAM 64GB以上・SSD空き80～100GB以上は運用上の目安であり、実測最小値ではありません。NVIDIAドライバーは利用者が用意してください。

Weights, runtimes, dependency binaries, personal histories, logs and local environment reports are excluded. Initial setup requires internet access and substantial free storage. RAM 64GB and 80–100GB free SSD space are planning guidance, not measured minimums. Install a compatible NVIDIA driver separately.

## ライセンス・サポート / License and support

独自コードはMIT。モデル等のライセンス確認・遵守と生成物の利用判断は利用者の責任です。サポートは可能な範囲で行いますが、結果保証・個別補償・クレーム対応は行いません。法令上制限できない権利・責任は除きます。詳細は [利用責任とサポート方針](USAGE_AND_SUPPORT.md)。

Original code: MIT. Users are responsible for licenses, permissions and output use. Support is best effort; results, complaint resolution and individual compensation are not promised, subject to mandatory law. See [Usage and support policy](USAGE_AND_SUPPORT.md).

[日本語マニュアル](docs/Manual_ja.md) · [Third-party notices](docs/THIRD_PARTY_NOTICES.md) · [Release notes](docs/RELEASE_NOTES.md)
