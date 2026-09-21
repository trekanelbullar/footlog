"""I7（静的な検査）：LLM 呼び出しの唯一の入口が ``llm.py`` であること（設計書 §6.1、【C-X2】）。

``OrcaClient``・``openai`` の import、``get_settings()``（OrcaRouter の鍵を持つ
設定）の呼び出し、``orcarouter`` という文字列の出現が、``llm.py``・``clients/``・
``config.py`` の外に無いことを確かめる。
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "ai_hackathon_team_a"

_ALLOWED_FILES = {
    _SRC_ROOT / "llm.py",
    _SRC_ROOT / "config.py",
    # cli.py は1段目からある、独立した ``ai-hackathon`` CLI（既存の generate を直接
    # 呼ぶだけの手動確認用ツール）。設計書 §6.2「既存の generate とテストは変えない」
    # により、worker のパイプライン（call_llm の唯一の入口）とは別の既存経路として
    # 除外する。
    _SRC_ROOT / "cli.py",
    # worker_settings.py は ``ORCAROUTER_`` という接頭辞の名前をドキュメントとして
    # 説明しているだけで、OrcaClient・openai の呼び出しは無い。
    _SRC_ROOT / "worker_settings.py",
}
_ALLOWED_DIRS = {_SRC_ROOT / "clients"}

_ORCA_CLIENT_IMPORT_RE = re.compile(r"\bOrcaClient\b")
_OPENAI_IMPORT_RE = re.compile(r"^\s*(?:import openai|from openai\b)", re.MULTILINE)
_GET_SETTINGS_CALL_RE = re.compile(r"\bget_settings\s*\(")
_ORCAROUTER_STRING_RE = re.compile(r"orcarouter", re.IGNORECASE)


def _is_allowed(path: Path) -> bool:
    if path in _ALLOWED_FILES:
        return True
    return any(allowed_dir in path.parents for allowed_dir in _ALLOWED_DIRS)


def _python_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_orca_client_referenced_only_inside_llm_clients_config() -> None:
    offenders = [
        str(path)
        for path in _python_files()
        if not _is_allowed(path) and _ORCA_CLIENT_IMPORT_RE.search(path.read_text(encoding="utf-8"))
    ]
    message = f"OrcaClient は llm.py・clients/・config.py の外から参照できません: {offenders}"
    assert offenders == [], message


def test_openai_imported_only_inside_llm_clients_config() -> None:
    offenders = [
        str(path)
        for path in _python_files()
        if not _is_allowed(path) and _OPENAI_IMPORT_RE.search(path.read_text(encoding="utf-8"))
    ]
    message = f"openai は llm.py・clients/・config.py の外から import できません: {offenders}"
    assert offenders == [], message


def test_get_settings_called_only_inside_llm_clients_config() -> None:
    offenders = [
        str(path)
        for path in _python_files()
        if not _is_allowed(path) and _GET_SETTINGS_CALL_RE.search(path.read_text(encoding="utf-8"))
    ]
    message = f"get_settings() は llm.py・clients/・config.py の外から呼べません: {offenders}"
    assert offenders == [], message


def test_orcarouter_string_appears_only_inside_llm_clients_config() -> None:
    offenders = [
        str(path)
        for path in _python_files()
        if not _is_allowed(path) and _ORCAROUTER_STRING_RE.search(path.read_text(encoding="utf-8"))
    ]
    message = f"'orcarouter' は llm.py・clients/・config.py の外に現れてはいけません: {offenders}"
    assert offenders == [], message
