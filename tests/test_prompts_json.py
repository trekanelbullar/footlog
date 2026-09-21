"""プロンプトのファイルには、必ず「JSON」という語を入れる（追加指示 AD-6 の周辺）。

qwen の提供元は、JSON 形式の出力を指定したリクエストで、メッセージに「json」という
語が無いと、リクエストそのものを弾く（2026-09-22 に組み立ての段階で実際に起きた）。
全段階が JSON 形式で呼ぶので、相方がプロンプトを差し替えたときにこの語を落とさない
よう、ここで確かめる。
"""

from pathlib import Path

import pytest

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@pytest.mark.parametrize("path", sorted(_PROMPTS_DIR.glob("*.txt")), ids=lambda p: p.name)
def test_every_prompt_mentions_json(path: Path) -> None:
    assert "json" in path.read_text(encoding="utf-8").lower()
