"""エージェントの1件分のループ：上限の直前に質問の機会が必ず来ること（C5）。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from ai_hackathon_team_a import agent


def _candidate() -> agent.AgentCandidate:
    return agent.AgentCandidate(
        event_id=uuid4(),
        event_no=1,
        kind="decision",
        summary="デモはダークモードで見せる",
        reason=None,
        occurred_at=datetime(2026, 9, 22, tzinfo=UTC),
        segment_ids=["S1-1"],
        partition="all",
    )


def test_last_round_offers_only_ask_member(monkeypatch) -> None:
    """探す道具を繰り返す LLM でも、最後の1回は ask_member だけが渡され、質問が送られる。"""

    monkeypatch.setattr(
        agent, "_build_initial_messages", lambda conn, **kw: [{"role": "user", "content": "x"}]
    )
    monkeypatch.setattr(agent, "_tool_search_events", lambda conn, **kw: {"events": []})
    asked: list[str] = []
    monkeypatch.setattr(
        agent, "_tool_ask_member", lambda conn, **kw: asked.append(kw["question"]) or True
    )
    offered: list[list[str]] = []

    def fake_llm(stage, messages, *, json_mode, tools):
        names = [t["function"]["name"] for t in tools or []]
        offered.append(names)
        name = "ask_member" if names == ["ask_member"] else "search_events"
        args = '{"question": "なぜですか？"}' if name == "ask_member" else '{"query": "デモ"}'
        call = {"id": f"c{len(offered)}", "function": {"name": name, "arguments": args}}
        return SimpleNamespace(tool_calls=[call], text="")

    recipient = agent._Recipient(user_id=uuid4(), email="m@example.com")
    _, asked_flag = agent._run_single_event_loop(
        None,
        project_id=uuid4(),
        run_id=uuid4(),
        candidate=_candidate(),
        llm_fn=fake_llm,
        goal_description="g",
        start_epoch=0,
        worker_settings=None,
        recipient=recipient,
        mail_transport=None,
    )

    assert offered[agent._MAX_TOOL_ROUNDS_PER_EVENT - 1] == ["ask_member"]
    assert "ask_member" in offered[0]
    assert asked == ["なぜですか？"]
    assert asked_flag is True


def test_last_round_keeps_normal_tools_without_recipient(monkeypatch) -> None:
    """質問できる相手がいなければ、最後の1回も普段の道具のまま（ask_member は出さない）。"""

    monkeypatch.setattr(
        agent, "_build_initial_messages", lambda conn, **kw: [{"role": "user", "content": "x"}]
    )
    monkeypatch.setattr(agent, "_tool_search_events", lambda conn, **kw: {"events": []})
    offered: list[list[str]] = []

    def fake_llm(stage, messages, *, json_mode, tools):
        offered.append([t["function"]["name"] for t in tools or []])
        if tools is None:
            return SimpleNamespace(tool_calls=None, text='{"resolved": false}')
        call = {"id": "c", "function": {"name": "search_events", "arguments": "{}"}}
        return SimpleNamespace(tool_calls=[call], text="")

    agent._run_single_event_loop(
        None,
        project_id=uuid4(),
        run_id=uuid4(),
        candidate=_candidate(),
        llm_fn=fake_llm,
        goal_description="g",
        start_epoch=0,
        worker_settings=None,
        recipient=None,
        mail_transport=None,
    )

    assert all("ask_member" not in names for names in offered)
