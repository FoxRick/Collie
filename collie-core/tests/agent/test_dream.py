"""Tests for Dream memory consolidation — build_dream_prompt and cursor management."""

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.providers.base import LLMResponse
from nanobot.utils.prompt_templates import render_template


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path)
    s.write_soul("# Soul\n- Helpful")
    s.write_memory("# Memory\n- Project X active")
    return s


class TestBuildDreamPrompt:
    def test_returns_none_when_no_history(self, store):
        assert store.build_dream_prompt() is None

    def test_returns_prompt_with_history(self, store):
        store.append_history("hello")
        result = store.build_dream_prompt()
        assert result is not None
        prompt, cursor = result
        assert cursor > 0
        assert "## Conversation History" in prompt
        assert "hello" in prompt

    def test_cursor_advances_only_new_entries(self, store):
        store.append_history("first")
        r1 = store.build_dream_prompt()
        assert r1 is not None
        _, c1 = r1

        # Cursor not yet advanced — same entries are still available
        assert store.build_dream_prompt() is not None

        # Advance cursor
        store.set_last_dream_cursor(c1)
        # Now no new entries
        assert store.build_dream_prompt() is None

        # Add new entry
        store.append_history("second")
        r2 = store.build_dream_prompt()
        assert r2 is not None
        _, c2 = r2
        assert c2 > c1

    def test_prompt_includes_skill_creator_path(self, store):
        store.append_history("test")
        result = store.build_dream_prompt()
        assert result is not None
        prompt, _ = result
        assert "skill-creator" in prompt

    def test_prompt_embeds_current_memory_file_contents(self, store):
        """Dream must see the real current file contents (Tier 4) so it edits the
        files, not a stale mental model."""
        store.append_history("hello")
        result = store.build_dream_prompt()
        assert result is not None
        prompt, _ = result
        assert "## Current Memory Files" in prompt
        assert "### SOUL.md" in prompt
        assert "### USER.md" in prompt
        assert "### memory/MEMORY.md" in prompt
        # Real current contents are embedded verbatim.
        assert "Project X active" in prompt
        assert "Helpful" in prompt

    def test_prompt_renders_missing_files_as_empty(self, tmp_path):
        store = MemoryStore(tmp_path)  # no durable files written
        store.append_history("hello")
        result = store.build_dream_prompt()
        assert result is not None
        prompt, _ = result
        assert "(empty)" in prompt

    def test_workspace_dream_prompt_overrides_default(self, store):
        store.dream_prompt_file.parent.mkdir(parents=True)
        store.dream_prompt_file.write_text(
            "Custom Dream prompt.",
            encoding="utf-8",
        )
        store.append_history("keep this fact")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert prompt.startswith("Custom Dream prompt.")
        assert "memory consolidation engine" not in prompt
        assert "## Conversation History" in prompt
        assert "keep this fact" in prompt

    def test_workspace_dream_prompt_override_is_capped(self, store):
        store.dream_prompt_file.parent.mkdir(parents=True)
        store.dream_prompt_file.write_text("x" * 40_000, encoding="utf-8")
        store.append_history("keep this fact")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert "x" * 40_000 not in prompt
        assert "... (truncated)" in prompt
        assert "## Conversation History" in prompt
        assert "keep this fact" in prompt

    def test_empty_workspace_dream_prompt_uses_default(self, store):
        store.dream_prompt_file.parent.mkdir(parents=True)
        store.dream_prompt_file.write_text("  \n", encoding="utf-8")
        store.append_history("test")

        result = store.build_dream_prompt()

        assert result is not None
        prompt, _ = result
        assert "memory consolidation engine" in prompt

    def test_truncates_long_entries(self, store):
        long_content = "x" * 2000
        store.append_history(long_content)
        result = store.build_dream_prompt()
        assert result is not None
        prompt, _ = result
        # The full 2000 chars should not appear — truncated to 500
        assert long_content not in prompt
        assert "x" * 500 in prompt

    def test_batches_oldest_unprocessed_entries_first(self, store):
        for i in range(25):
            store.append_history(f"entry-{i + 1:02d}")

        result = store.build_dream_prompt(max_entries=20)
        assert result is not None
        prompt, cursor = result

        assert cursor == 20
        assert "entry-01" in prompt
        assert "entry-20" in prompt
        assert "entry-21" not in prompt

        store.set_last_dream_cursor(cursor)
        next_result = store.build_dream_prompt(max_entries=20)
        assert next_result is not None
        next_prompt, next_cursor = next_result
        assert next_cursor == 25
        assert "entry-21" in next_prompt
        assert "entry-25" in next_prompt

    def test_skips_malformed_history_entries(self, store):
        """Dream prompt building should tolerate externally corrupted JSONL rows."""
        store.history_file.write_text(
            '{"cursor": 1, "timestamp": "2026-04-01 10:00"}\n'
            '{"cursor": 2, "timestamp": "2026-04-01 10:01", "content": "usable memory"}\n',
            encoding="utf-8",
        )

        result = store.build_dream_prompt()

        assert result is not None
        prompt, cursor = result
        assert cursor == 2
        assert "usable memory" in prompt

    def test_dream_prompt_consumes_consolidator_attribute_tags(self):
        prompt = render_template(
            "agent/dream.md",
            strip=True,
            skill_creator_path="skills/skill-creator/SKILL.md",
        )

        assert "History attribute tags" in prompt
        assert "[skip]: audit-only" in prompt
        assert "[correction]: replace the older conflicting fact" in prompt
        assert "Always strip these bracketed tags from saved memory content" in prompt


class TestEphemeralDirect:
    """Tests for the ephemeral flag that skips history.jsonl writes for Dream."""

    @pytest.fixture
    def _make_loop(self, tmp_path):
        """Factory fixture that builds a minimal AgentLoop with mocked deps."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from nanobot.agent.loop import AgentLoop
        from nanobot.agent.memory import MemoryStore
        from nanobot.bus.queue import MessageBus

        store = MemoryStore(tmp_path)
        store.write_soul("# Soul")
        store.write_memory("# Memory")

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.supports_tools = True
        provider.generation = MagicMock(max_tokens=4096)
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(content="done", tool_calls=[], finish_reason="stop", usage={})
        )

        with (
            patch("nanobot.agent.loop.SessionManager"),
            patch("nanobot.agent.loop.SubagentManager") as mock_sub,
            patch("nanobot.agent.loop.Consolidator") as mock_consolidator_cls,
        ):
            mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
            mock_consolidator_cls.return_value.maybe_consolidate_by_tokens = AsyncMock()
            loop = AgentLoop(
                bus=bus,
                provider=provider,
                workspace=tmp_path,
                context_window_tokens=8000,
            )

        return loop, store

    async def test_ephemeral_skips_raw_archive(self, tmp_path, _make_loop):
        """When ephemeral=True, raw_archive must not be called."""
        from unittest.mock import patch

        loop, store = _make_loop

        with patch.object(loop.context.memory, "raw_archive") as mock_archive:
            await loop.process_direct(
                "test", session_key="dream:test", ephemeral=True,
            )
            mock_archive.assert_not_called()

    async def test_non_ephemeral_runs_normally(self, tmp_path, _make_loop):
        """Without ephemeral, the normal path returns the model response."""
        loop, store = _make_loop
        response = await loop.process_direct("test", session_key="cli:normal")

        assert response is not None
        assert response.content == "done"
        loop.provider.chat_with_retry.assert_awaited()

    async def test_ephemeral_sets_ctx_flag(self, tmp_path, _make_loop):
        """Verify that ephemeral=True is forwarded to TurnContext."""
        from unittest.mock import patch

        loop, store = _make_loop

        captured = {}

        original_save = loop._state_save

        async def patched_save(ctx):
            captured["ephemeral"] = ctx.ephemeral
            return await original_save(ctx)

        with patch.object(loop, "_state_save", side_effect=patched_save):
            await loop.process_direct(
                "test", session_key="dream:check", ephemeral=True,
            )

        assert captured.get("ephemeral") is True

    async def test_default_ephemeral_is_false(self, tmp_path, _make_loop):
        """By default ephemeral is False in TurnContext."""
        from unittest.mock import patch

        loop, store = _make_loop

        captured = {}

        original_save = loop._state_save

        async def patched_save(ctx):
            captured["ephemeral"] = ctx.ephemeral
            return await original_save(ctx)

        with patch.object(loop, "_state_save", side_effect=patched_save):
            await loop.process_direct("test", session_key="cli:normal")

        assert captured.get("ephemeral") is False

    async def test_ephemeral_skips_consolidator(self, tmp_path, _make_loop):
        """When ephemeral=True, consolidator.maybe_consolidate_by_tokens is not called."""
        from unittest.mock import patch

        loop, store = _make_loop

        with patch.object(
            loop.consolidator, "maybe_consolidate_by_tokens",
        ) as mock_consolidate:
            await loop.process_direct(
                "test", session_key="dream:consolidate-test", ephemeral=True,
            )
            mock_consolidate.assert_not_called()

    async def test_ephemeral_response_reports_stop_reason(self, tmp_path, _make_loop):
        loop, store = _make_loop
        loop.provider.chat_with_retry.return_value = LLMResponse(
            content="provider error",
            finish_reason="error",
        )

        resp = await loop.process_direct(
            "test", session_key="dream:error", ephemeral=True,
        )

        assert resp is not None
        assert resp.metadata["_stop_reason"] == "error"
        assert MemoryStore.dream_run_completed(resp) is False

class TestEphemeralHooks:
    """When ephemeral=True, extra hooks must not fire."""

    @pytest.fixture
    def _make_loop_with_spy(self, tmp_path):
        """Build an AgentLoop with a spy hook to verify hook firing behavior."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from nanobot.agent.hook import AgentHook
        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.supports_tools = True
        provider.generation = MagicMock(max_tokens=4096)
        provider.chat_with_retry = AsyncMock(
            return_value=MagicMock(
                content="done", finish_reason="stop", tool_calls=[], usage={},
            )
        )

        spy = MagicMock(spec=AgentHook)
        spy.wants_streaming.return_value = False
        spy.before_iteration = AsyncMock()
        spy.after_iteration = AsyncMock()

        with (
            patch("nanobot.agent.loop.SessionManager"),
            patch("nanobot.agent.loop.SubagentManager") as mock_sub,
            patch("nanobot.agent.loop.Consolidator") as mock_consolidator_cls,
        ):
            mock_sub.return_value.cancel_by_session = AsyncMock(return_value=0)
            mock_consolidator_cls.return_value.maybe_consolidate_by_tokens = AsyncMock()
            loop = AgentLoop(
                bus=bus,
                provider=provider,
                workspace=tmp_path,
                context_window_tokens=8000,
                hooks=[spy],
            )

        return loop, spy

    async def test_extra_hooks_skipped_when_ephemeral(self, tmp_path, _make_loop_with_spy):
        """When ephemeral=True, extra hooks must not fire."""
        loop, spy = _make_loop_with_spy

        await loop.process_direct(
            "test", session_key="dream:hook-test", ephemeral=True,
        )
        spy.before_iteration.assert_not_called()
        spy.after_iteration.assert_not_called()

    async def test_extra_hooks_fire_for_normal_sessions(self, tmp_path, _make_loop_with_spy):
        """Without ephemeral, extra hooks should fire normally."""
        loop, spy = _make_loop_with_spy

        await loop.process_direct("test", session_key="cli:normal")
        spy.before_iteration.assert_called()


