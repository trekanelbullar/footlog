"""``assemble_and_judge`` の ``allow_regeneration``（AD-7）。

手動実行（``allow_regeneration=False``）では Judge が不合格でもやり直さず、
``judge_status = flagged`` を返す。ASSEMBLE は1回しか呼ばれないことを、呼ばれた
段階を記録する偽の LLM で確かめる。既定（``allow_regeneration=True``、定期実行
相当）では、これまでどおり1回だけやり直し、ASSEMBLE が2回呼ばれる。
"""

import json

from ai_hackathon_team_a.pipeline.contracts import DIFF_HEADINGS, AssembleInput, EventSummary
from ai_hackathon_team_a.pipeline.judge import assemble_and_judge

_ASSEMBLE_OUTPUT = json.dumps(
    {
        "sections": {
            "decisions": [{"text": "Xを採用", "event_nos": [1]}],
            "reasons": [],
            "rejected_options": [],
            "next_steps": [],
            "current_status": [],
        },
        "summary_for_mail": "要約",
    }
)
_JUDGE_FAIL = json.dumps({"status": "fail", "notes": ["理由が薄い"]})
_JUDGE_PASS = json.dumps({"status": "pass", "notes": []})


class _Result:
    def __init__(self, text: str) -> None:
        self.text = text


def _make_input() -> AssembleInput:
    return AssembleInput(
        mode="diff",
        goal_description="G",
        events=[EventSummary(event_no=1, kind="decision", summary="Xを採用", reason="安いから")],
        new_event_nos=[1],
    )


def _make_llm(judge_texts: list[str]) -> tuple[list[str], object]:
    """段階の呼び出し順を ``calls`` に記録しつつ、JUDGE には ``judge_texts`` を順に返す。"""

    calls: list[str] = []
    remaining_judge = list(judge_texts)

    def _llm(stage: str, messages, *, json_mode: bool = False, tools=None) -> _Result:
        calls.append(stage)
        if stage == "ASSEMBLE":
            return _Result(_ASSEMBLE_OUTPUT)
        if stage == "JUDGE":
            return _Result(remaining_judge.pop(0))
        raise AssertionError(f"unexpected stage {stage}")

    return calls, _llm


def test_manual_run_does_not_regenerate_and_flags_on_fail() -> None:
    calls, llm = _make_llm([_JUDGE_FAIL])

    result = assemble_and_judge(
        _make_input(),
        llm,
        headings=DIFF_HEADINGS,
        valid_event_nos={1},
        unsupported_event_nos=set(),
        allow_regeneration=False,
    )

    assert calls.count("ASSEMBLE") == 1
    assert calls.count("JUDGE") == 1
    assert result.judge_status == "flagged"


def test_scheduled_run_regenerates_once_on_fail() -> None:
    calls, llm = _make_llm([_JUDGE_FAIL, _JUDGE_PASS])

    result = assemble_and_judge(
        _make_input(),
        llm,
        headings=DIFF_HEADINGS,
        valid_event_nos={1},
        unsupported_event_nos=set(),
        allow_regeneration=True,
    )

    assert calls.count("ASSEMBLE") == 2
    assert calls.count("JUDGE") == 2
    assert result.judge_status == "pass"


def test_allow_regeneration_defaults_to_true() -> None:
    calls, llm = _make_llm([_JUDGE_FAIL, _JUDGE_PASS])

    result = assemble_and_judge(
        _make_input(),
        llm,
        headings=DIFF_HEADINGS,
        valid_event_nos={1},
        unsupported_event_nos=set(),
    )

    assert calls.count("ASSEMBLE") == 2
    assert result.judge_status == "pass"
