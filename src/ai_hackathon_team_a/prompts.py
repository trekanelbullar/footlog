"""プロンプトの読み込み（設計書 §6.3）。

``PROMPTS_DIR``（既定はリポジトリ直下の ``prompts/``）から6ファイルを読み込む。
起動時に呼び、足りないファイルがあれば起動を失敗させる。差し込む値は
``{goal_description}`` のような名前付きの穴だけで、資料の本文は穴に入れない
（呼び出し側がユーザーメッセージ側に ``<source>`` で囲んで渡す）。
"""

import os
from functools import lru_cache
from pathlib import Path

# repo 直下の prompts/（このファイルは src/ai_hackathon_team_a/ 配下にある）。
# ``pipeline/`` は DB に触れない純粋な関数のままにするため（設計書 §6.4）、ここでは
# ``WorkerSettings`` を経由せず、``PROMPTS_DIR`` を直接読む（既定は同じ場所）。
_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

PROMPT_NAMES: tuple[str, ...] = (
    "extract",
    "support_check",
    "agent",
    "assemble_diff",
    "assemble_baseline",
    "judge",
)


class PromptLoadError(RuntimeError):
    """``prompts/`` のファイルが読めない・足りないことを表す。"""


def load_prompts(prompts_dir: Path) -> dict[str, str]:
    """``prompts/`` の6ファイルを全部読み込む。1つでも無ければ :class:`PromptLoadError`。"""

    prompts: dict[str, str] = {}
    missing: list[str] = []
    for name in PROMPT_NAMES:
        path = prompts_dir / f"{name}.txt"
        if not path.is_file():
            missing.append(str(path))
            continue
        prompts[name] = path.read_text(encoding="utf-8")
    if missing:
        raise PromptLoadError(f"prompts が見つかりません: {missing}")
    return prompts


def render(template: str, **values: str) -> str:
    """名前付きの穴（``{goal_description}`` など）だけを埋める。"""

    return template.format(**values)


@lru_cache
def get_prompts() -> dict[str, str]:
    """``PROMPTS_DIR``（既定はリポジトリ直下の ``prompts/``）から読み込み、キャッシュする。

    ``pipeline/`` の各段階の純粋関数から呼ばれる。起動時の存在チェック
    （API 起動時に ``load_prompts`` を直接呼ぶもの）とは別に、実行時にも遅延で
    読み込めるようにするための入口。
    """

    override = os.environ.get("PROMPTS_DIR")
    directory = Path(override) if override else _DEFAULT_PROMPTS_DIR
    return load_prompts(directory)
