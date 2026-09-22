# LLM モデル比較の評価

本番パイプライン（`src/ai_hackathon_team_a/pipeline/`）の `extract_events` / `check_support` /
`assemble_report` / `judge_report` / `assemble_and_judge` を、`llm` 引数だけ評価用クライアントに
差し替えて呼ぶ。プロンプトは本番の `prompts/*.txt` をそのまま使う。DB には一切つながない。

## 中身
- `results/models.md` — OrcaRouter の `GET /models` の全件と単価（費用計算はこの表を実行時に読む）。
- `results/candidates.md` — 候補6モデルと選定理由。
- `datasets/case_*.json` — 抽出用の評価データ6種類（正解の出来事つき）。
- `datasets/support_cases.json` — 裏付け検査用の4件。実行時に、抽出データの正解から
  20件（正しい根拠＝supported / 別ケースの根拠＝unsupported）を決定的に追加で作る。
- `datasets/judge_cases.json` — Judge 用の8件（合格4・不合格4、人手ラベル）。
- `eval-config.json` — 候補・繰り返し回数・費用上限・段階ごとの出力上限・思考 off の送り方。
- `run_eval.py` — 実行・採点（コードのみ）・出力。

## 流し方（リポジトリ直下で）
```
uv run python eval/run_eval.py smoke                        # 各モデル1回だけ
uv run python eval/run_eval.py extract                      # 段階1：抽出、6モデル×6ケース×3回
uv run python eval/run_eval.py extract --reasoning low --models A,B   # 上位2モデルの思考 low
uv run python eval/run_eval.py stage2                       # 段階2：最安3モデルで support/assemble/judge
uv run python eval/run_eval.py configs --good <model> --cheap <model> # 段階3：構成 A/B/C
uv run python eval/run_eval.py report                       # 集計だけやり直す
```
- 呼び出しごとの記録は `results/calls.jsonl`、単位ごとの結果は `results/runs.jsonl` に追記される。
  同じ単位は再実行時に飛ばすので、止まっても続きから流せる。
- 費用上限（5 USD）は `calls.jsonl` の累計から計算し、プロセスをまたいで守る。呼ぶ前に
  「入力1文字=1トークン、出力=max_tokens 全部」の最悪値を予約し、超えるなら呼ばずに止める。
- 出力：`results/results.md`（表・推奨）、`results/results.csv`（単位ごと）、
  `results/cost_vs_quality.svg`（費用×品質の散布図。matplotlib が無いため手書き SVG）。

## 構成 A・B・C
| 構成 | extract | support | assemble | judge |
|---|---|---|---|---|
| **A** | good | good | good | good |
| **B** | good | cheap | cheap | cheap |
| **C** | cheap（unsupported と判定された出来事だけ good で再抽出） | cheap | cheap | cheap |

- good＝段階1で抽出の成績が最良かつ極端に高価でないモデル。cheap＝十分な品質を保つ最安モデル。
- C の再抽出：塊全体を good で抽出し直し、unsupported の出来事と根拠の区切りが重なる出来事で
  置き換える（重なる出来事が無ければその出来事は捨てる）。置き換えた出来事は cheap で再度裏付け検査する。

## 採点（すべてコード、LLM 不使用）
- **抽出**：kind が一致し、正解の根拠の区切り番号を含めば一致（1対1の貪欲対応）。全ケース合算の
  precision / recall / F1、JSON 有効率、出どころ正解率、作業ログのみのケースでの誤抽出数、遅延、費用。
- **裏付け検査**：正解ラベルとの一致。スコア＝1−(誤 supported＋誤棄却)/件数。
- **組み立て**：JSON・5見出し・根拠番号付きの文の率・出来事の網羅率・決定/却下の見出し配置・
  上限（5文/300字/メール要約）の平均。
- **Judge**：人手ラベルとの一致率、誤合格・誤不合格。
- **構成 A/B/C**：(抽出F1 ＋ 組み立てスコア ＋ Judge 合格率)/3。

## 思考 off の送り方（2026-09-22 実測）
- qwen：`enable_thinking=false`（本番の `clients/orca.py` と同じ）。
- deepseek-v4.1-flash：既定で思考する。`thinking={"type":"disabled"}` で止まる（本番コードは未対応）。
- glm-5.3-flash：試した方法ではどれも止まらない（一部は 400）。出力上限 4000 では思考だけで
  上限に達して本文が空になるため、評価では上限を 16000 に引き上げて実行している。
