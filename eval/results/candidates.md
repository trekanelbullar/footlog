# 候補モデル（6つ）

`orcarouter/auto`（ルーティング先が固定できないため測定に向かない）は除外。単価は
`eval/results/models.md`（`GET {base_url}/models` の応答の `pricing.prompt_per_million` /
`completion_per_million`、取得日 2026-09-22）より。1M tok あたり USD。

| 役割 | モデルID | 入力 | 出力 | 選んだ理由 |
|---|---|---|---|---|
| 現行（baseline） | `qwen/qwen3.7-flash` | $0.030 | $0.130 | 今の本番モデル。比較の基準点。 |
| 軽量（Google） | `google/gemini-2.5-flash-lite` | $0.100 | $0.400 | Google の軽量モデルの中で単価が最も低い部類（`gemini-3.5-flash-lite`は$0.300/$2.500）。世代は少し古いが、まず安価な選択肢として比較したい。 |
| 軽量（Zhipu） | `z-ai/glm-5.3-flash` | $0.075 | $0.250 | 候補中で最安値クラス。現行の qwen3.7-flash に近い価格帯で、別ベンダーの精度を見たい。 |
| 軽量（DeepSeek） | `deepseek/deepseek-v4.1-flash` | $0.150 | $0.600 | DeepSeek の最新軽量モデル。旧世代の `deepseek-v4-flash`（$0.220/$0.660）より安く新しい。 |
| 軽量（OpenAI） | `openai/gpt-5.4-nano` | $0.200 | $1.250 | OpenAI 系では最軽量帯。`gpt-4.1-mini`（$0.400/$1.600）より安価で世代も新しい。 |
| 上限の目安 | `anthropic/claude-opus-5` | $5.000 | $25.000 | 精度の天井を見るための上位モデル。`openai/gpt-5.2-pro`（$21/$168）ほど極端に高くなく、他候補と同じ土俵で複数回・複数段階を回しても費用上限（5 USD）内に収めやすい。 |

## 選定方針
- 各社（Alibaba / Google / Zhipu / DeepSeek / OpenAI / Anthropic）から1つずつになるようにし、
  ベンダーロックインの偏りを避けた。
- 「軽量」は各社の最安値ではなく、精度とのバランスが取れそうな下位〜中位モデルを選んだ
  （最安値のものは指示追従が弱く抽出タスクに向かない可能性があるため）。
- 上限モデルは1つだけとし、費用上限（5 USD、繰り返し3回 × 6データセット × 4段階）を
  超えないよう、最上位（`gpt-5.2-pro`, `gpt-5.4-pro`, `gemini-3.1-pro-preview` 等）ではなく
  中間の高性能モデルを選んだ。

## 単価が取得できなかった候補（参考、今回は候補に含めず）
以下は `GET /models` の応答にトークン単価が無い（`request` 単位＝画像・動画生成や
無料枠などのため）。今回の6候補には含めていないが、参考までに一覧化する。
利用する場合は OrcaRouter の管理画面で確認すること。

- `orcarouter/free`
- `orcarouter/fusion`
- `orcarouter/fusion-flash`
- `orcarouter/fusion-mini`
- `orcarouter/auto`（除外指定のため対象外）
- `deepseek/deepseek-v4-flash-free`
- `tencent/hy3-free`
- `z-ai/glm-5.3-flash-free`
- 画像・動画生成系（`google/imagen-*`, `kling/*`, `grok/grok-imagine-image`,
  `google/gemini-3-pro-image-preview`, `google/gemini-3.1-flash-image-preview` など）
- 秒/分単位課金の音声・動画系（`minimax/minimax-h3`, `orca/dub`）

全24件の詳細は `eval/results/models.md` の末尾を参照。
