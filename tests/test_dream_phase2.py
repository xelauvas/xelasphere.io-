"""Tests for Phase 2 Kimi LLM consolidation."""
import pytest
import json
from unittest.mock import MagicMock, patch


class TestBuildPrompt:
    def test_builds_prompt_with_conflicts(self):
        from app.memory_v2.dream.phase2 import build_consolidation_prompt
        from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
        memories = [
            MemoryItem(id='m1', key='timezone', value='PST', type=MemoryType.PROFILE, confidence=0.8, status=MemoryStatus.ACTIVE),
            MemoryItem(id='m2', key='timezone', value='GMT', type=MemoryType.PROFILE, confidence=0.9, status=MemoryStatus.ACTIVE),
        ]
        prompt = build_consolidation_prompt(
            flagged_conflicts=[['m1', 'm2']], flagged_low_value=[],
            all_active=memories, memories_by_id={'m1': memories[0], 'm2': memories[1]},
        )
        assert 'timezone' in prompt
        assert 'PST' in prompt
        assert 'GMT' in prompt

    def test_builds_prompt_with_low_value(self):
        from app.memory_v2.dream.phase2 import build_consolidation_prompt
        from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
        mem = MemoryItem(id='m1', key='old_project', value='legacy app', type=MemoryType.PROJECT, confidence=0.6, status=MemoryStatus.ACTIVE)
        prompt = build_consolidation_prompt(
            flagged_conflicts=[], flagged_low_value=['m1'],
            all_active=[mem], memories_by_id={'m1': mem},
        )
        assert 'old_project' in prompt

    def test_prompt_within_token_budget(self):
        from app.memory_v2.dream.phase2 import build_consolidation_prompt
        from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
        memories = []
        by_id = {}
        for i in range(50):
            mem = MemoryItem(id=f'm{i}', key=f'key_{i}', value=f'value_{i}' * 20, type=MemoryType.PREFERENCE, confidence=0.7, status=MemoryStatus.ACTIVE)
            memories.append(mem)
            by_id[f'm{i}'] = mem
        prompt = build_consolidation_prompt(
            flagged_conflicts=[], flagged_low_value=[],
            all_active=memories, memories_by_id=by_id, token_budget=4000,
        )
        assert len(prompt) < 20000


class TestParseKimiResponse:
    def test_parses_valid_response(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        raw = json.dumps({
            "resolve_conflicts": [{"keep_id": "m1", "supersede_ids": ["m2"], "reason": "newer"}],
            "prune": [{"memory_id": "m3", "reason": "stale"}],
            "promote": [{"memory_id": "m4", "reason": "corroborated"}],
            "synthesize": [{"type": "preference", "key": "style", "value": "iterative", "evidence_ids": ["m5", "m6"], "confidence": 0.75}],
        })
        result = parse_kimi_response(raw)
        assert len(result['resolve_conflicts']) == 1
        assert len(result['prune']) == 1
        assert len(result['promote']) == 1
        assert len(result['synthesize']) == 1

    def test_handles_malformed_json(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        result = parse_kimi_response("not json at all")
        assert result['resolve_conflicts'] == []
        assert result['prune'] == []
        assert result['promote'] == []
        assert result['synthesize'] == []

    def test_handles_partial_response(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        raw = json.dumps({"resolve_conflicts": [], "prune": []})
        result = parse_kimi_response(raw)
        assert result['promote'] == []
        assert result['synthesize'] == []

    def test_caps_syntheses(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        raw = json.dumps({
            "resolve_conflicts": [], "prune": [], "promote": [],
            "synthesize": [{"type": "preference", "key": f"k{i}", "value": f"v{i}", "evidence_ids": [], "confidence": 0.7} for i in range(10)],
        })
        result = parse_kimi_response(raw, max_syntheses=3)
        assert len(result['synthesize']) == 3


class TestCallKimi:
    @patch('app.memory_v2.dream.phase2.requests.post')
    def test_call_kimi_success(self, mock_post):
        from app.memory_v2.dream.phase2 import call_kimi
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'choices': [{'message': {'content': '{"resolve_conflicts":[],"prune":[],"promote":[],"synthesize":[]}'}}]}
        mock_post.return_value = mock_response
        result = call_kimi("test prompt")
        assert result is not None

    @patch('app.memory_v2.dream.phase2.requests.post')
    def test_call_kimi_failure_returns_none(self, mock_post):
        from app.memory_v2.dream.phase2 import call_kimi
        mock_post.side_effect = Exception("connection timeout")
        result = call_kimi("test prompt")
        assert result is None


class TestShouldRunPhase2:
    def test_runs_when_conflicts_flagged(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport(flagged_conflicts=[['m1', 'm2']])
        assert should_run_phase2(report, pattern_threshold=15) is True

    def test_runs_when_low_value_flagged(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport(flagged_low_value=['m1'])
        assert should_run_phase2(report, pattern_threshold=15) is True

    def test_runs_when_many_active(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        from app.memory_v2.schema import MemoryItem
        report = ConsolidationReport(all_active=[MemoryItem() for _ in range(20)])
        assert should_run_phase2(report, pattern_threshold=15) is True

    def test_skips_when_nothing_to_do(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport(all_active=[])
        assert should_run_phase2(report, pattern_threshold=15) is False
