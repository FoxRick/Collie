from nanobot.agent.context import ContextBuilder


def test_shared_context_excludes_private_bootstrap_memory_and_skills(tmp_path):
    (tmp_path / "VISION.md").write_text("PRIVATE_BOOTSTRAP_SENTINEL", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("PRIVATE_MEMORY_SENTINEL", encoding="utf-8")
    builder = ContextBuilder(tmp_path)
    messages = builder.build_messages(
        history=[{"role": "user", "content": "PRIVATE_SESSION_SENTINEL"}],
        current_message="published request",
        session_metadata={
            "audience_mode": "shared",
            "published_history": [{"role": "user", "content": "published history"}],
        },
    )
    serialized = repr(messages)
    assert "published history" in serialized
    assert "published request" in serialized
    assert "PRIVATE_BOOTSTRAP_SENTINEL" not in serialized
    assert "PRIVATE_MEMORY_SENTINEL" not in serialized
    assert "PRIVATE_SESSION_SENTINEL" not in serialized


def test_private_result_uses_requesters_private_context_for_review(tmp_path):
    (tmp_path / "VISION.md").write_text("PRIVATE_BOOTSTRAP_SENTINEL", encoding="utf-8")
    builder = ContextBuilder(tmp_path)
    messages = builder.build_messages(
        history=[], current_message="private task",
        session_metadata={
            "audience_mode": "private_result",
            "published_history": [
                {"role": "user", "author_id": "alice", "content": "shared input"}
            ],
        },
    )
    serialized = repr(messages)
    assert "PRIVATE_BOOTSTRAP_SENTINEL" in serialized
    assert "shared input" in serialized
    assert "Author: alice" in serialized
