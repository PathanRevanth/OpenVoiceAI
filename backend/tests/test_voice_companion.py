from voicefirst.agents.factory import build_voice_companion
from voicefirst.settings import Settings


def test_voice_companion_is_speech_only():
    agent = build_voice_companion(Settings(default_model="demo"))
    assert agent.name == "voice-companion"
    tools = getattr(agent, "toolset", None) or getattr(agent, "_toolset", None)
    # No specialist tools — work is the chat orchestrator.
    names = []
    if tools is not None:
        names = [getattr(t, "name", "") for t in getattr(tools, "tools", {}).values()] if hasattr(tools, "tools") else []
    assert "delegate_task" not in names
