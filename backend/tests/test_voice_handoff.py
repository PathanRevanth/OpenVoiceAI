from voicefirst.api.voice_handoff import should_handoff_to_orchestrator, spoken_control_cue


def test_fillers_do_not_handoff():
    assert not should_handoff_to_orchestrator("yeah")
    assert not should_handoff_to_orchestrator("ok thanks")
    assert not should_handoff_to_orchestrator("hi")


def test_work_utterances_handoff():
    assert should_handoff_to_orchestrator("hey orchestrator")
    assert should_handoff_to_orchestrator("can you design a marketing artifact")
    assert should_handoff_to_orchestrator("change the colour to white")
    assert should_handoff_to_orchestrator("search GitHub for pydantic-ai")
    assert should_handoff_to_orchestrator("please research when the Eiffel Tower was built")


def test_conversation_and_questions_stay_with_voice_model():
    # The voice model answers these itself; handing them off would make it
    # speak a duplicate answer when the background result lands.
    assert not should_handoff_to_orchestrator("what is two plus two")
    assert not should_handoff_to_orchestrator("what is the capital of France")
    assert not should_handoff_to_orchestrator("how are you doing today")
    assert not should_handoff_to_orchestrator("how do I fix a leaking tap")
    assert not should_handoff_to_orchestrator("tell me about the Eiffel Tower")


def test_spoken_cues_cover_run_lifecycle():
    assert "background" in (spoken_control_cue("run.started", {}) or "")
    assert "marketing" in (spoken_control_cue("tool.started", {"tool": "marketing"}) or "")
    done = spoken_control_cue("run.finished", {"status": "completed", "summary": "Draft is ready."})
    assert done and "Draft is ready" in done
