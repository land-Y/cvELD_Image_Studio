# 技術資料・参照元
確認日：2026-09-21。公開一次資料のみ。モデル版と実装版は固定し、上流のmainへの自動更新はしません。

| 対象 | 参照先 | 用途 |
|---|---|---|
| ユーザー指定の告知 | https://x.com/alibaba_qwen/status/2101659302792679789 | モデルの特定 |
| Qwen公式リポジトリ | https://github.com/QwenLM/Qwen-Image-2.1 | 7B Transformer、8B encoder、RGBA、画像生成・編集、参照枚数 |
| 公式モデル | https://huggingface.co/Qwen/Qwen-Image-2.1 | モデルの取得元 |
| 固定モデルコミット | https://huggingface.co/Qwen/Qwen-Image-2.1/commit/790c92633540aa0cb11d9abf19eb46d861714758 | モデル版の再現性 |
| モデルライセンス | https://huggingface.co/Qwen/Qwen-Image-2.1/blob/790c92633540aa0cb11d9abf19eb46d861714758/LICENSE | 原文を初回に取得して表示。WebUIのMITとは別 |
| Diffusers対応PR | https://github.com/huggingface/diffusers/pull/14804 | パイプライン追加・統合 |
| 固定パイプライン | https://github.com/huggingface/diffusers/blob/6256aa7666cedd47443adc8f82da9a10e110b09c/src/diffusers/pipelines/qwenimage21/pipeline_qwenimage21.py | 推論の公開引数、encode_prompt、RGBA、offload順序 |
| 固定Attention実装 | https://github.com/huggingface/diffusers/blob/6256aa7666cedd47443adc8f82da9a10e110b09c/src/diffusers/models/transformers/transformer_qwenimage21.py | exact segmented SDPA、KVキャッシュ、FlexAttentionの制約 |
| API文書 | https://huggingface.co/docs/diffusers/main/en/api/pipelines/qwenimage21 | 公開パイプラインの用例 |
| Diffusersメモリ管理 | https://huggingface.co/docs/diffusers/main/en/optimization/memory | CPUオフロード・VAEタイルの方式と制約 |
| PyTorch公式バージョン | https://pytorch.org/get-started/previous-versions/ | CUDA12.8版torch2.11.0とtorchvision0.26.0 |
| PyTorch CUDA12.8 wheel一覧 | https://download.pytorch.org/whl/cu128/torch/ | Python3.12、Windows AMD64版の存在確認 |
| Transformers | https://pypi.org/project/transformers/5.17.0/ | 固定バージョンの存在確認 |
| uv固定リリース | https://github.com/astral-sh/uv/releases/tag/0.12.17 | 公式Windowsスタンドアロン配布 |
| uv配布ハッシュ | https://github.com/astral-sh/uv/releases/expanded_assets/0.12.17 | Windows ZIPのSHA-256 |
| uv管理Python | https://docs.astral.sh/uv/guides/install-python/ | アプリ専用のPython導入 |
| Microsoft VC++ | https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170 | 最新v14 x64公式配布リンク |
| 上流の既知報告 | https://github.com/huggingface/diffusers/issues/14824 | MPSでの編集解像度に関する報告。Windows/CUDA再現は未確認 |

## 独自設計部分
FastAPIサーバー、バニラJavaScriptの日本語UI、キュー、キャンセル、PNG/JSON保存、部分マスクの合成、入力検証、ダウンロード検証、Windowsブートストラップは、この依頼向けに新規作成したコードです。独自部分について、公式QwenやDiffusersが動作保証しているという意味ではありません。

RAM64GB・空きSSD80～100GBは運用計画上の推奨目安で、公式公表の必須最小要件や実測値ではありません。推論時間・GPU利用量の性能値は推測して記載していません。
