"""tests/test_chatbot_summary.py — chatbot 요약(구간별 직렬 요약·줄 단위 스트리밍) 흐름 검증.

실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_chatbot_summary -v
LLM 체인·벡터 저장소·auth는 대역으로 바꾸고, 토큰 수와 단계 상한은 작게 고정해 구간 수를 정한다."""
import asyncio
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from hyundap.chatbot import citations as cit
from hyundap.chatbot import summary
from hyundap.chatbot.models import ChatbotError
from hyundap.embedding.models import VectorHit
from hyundap.search.models import ChunkHit


def vhit(i):
    return VectorHit(f'c{i}', f'본문 {i}', {
        'document_id': 'd1', 'version_id': 'v1', 'company_id': 'co', 'company_name': 'A사', 'doc_type': 'policy',
        'file_name': '규정.pdf', 'section_no': None, 'location_label': f'p.{i + 1}', 'chunk_index': i})


class FakeChain:
    """단계별 응답을 차례로 돌려주고 호출을 기록하는 체인 대역. stream은 글자 4개씩 쪼개 보낸다."""

    def __init__(self, log, replies, stream_error=None):
        self.log, self.replies, self.stream_error = log, replies, stream_error

    def invoke(self, values):
        self.log.append(('invoke', values['evidence']))
        return self.replies.pop(0)

    def stream(self, values):
        if self.stream_error:
            raise self.stream_error
        self.log.append(('stream', values['evidence']))
        text = self.replies.pop(0)
        for i in range(0, len(text), 4):
            yield text[i:i + 4]


class BadRequestError(Exception):
    pass


class SummaryFlowTests(unittest.TestCase):
    def run_summary(self, n_hits, replies, limit=10_000, tokens=100, stream_error=None, overflow_first=False):
        log, stages = [], []
        state = {'overflow': overflow_first}

        def chain_for(stage):
            stages.append(stage)
            chain = FakeChain(log, replies, stream_error)
            if state['overflow']:
                state['overflow'] = False
                chain.stream = lambda values: (_ for _ in ()).throw(BadRequestError('context_length_exceeded'))
            return chain

        with ExitStack() as stack:
            stack.enter_context(patch('hyundap.embedding.vector_store.get_document_chunks', return_value=[vhit(i) for i in range(n_hits)]))
            stack.enter_context(patch('hyundap.auth.permissions.verify_documents_access'))
            stack.enter_context(patch.object(summary, 'build_summary_chain', side_effect=chain_for))
            stack.enter_context(patch.object(summary, 'count_summary_tokens', return_value=tokens))
            stack.enter_context(patch.object(summary, 'step_token_limit', return_value=limit))
            events = []
            try:
                for event in summary.summarize_document_stream('d1', {'user_id': 'u'}):
                    events.append(event)
            except ChatbotError as error:
                events.append({'type': 'error', 'code': error.code})
        return events, log, stages

    def text_of(self, events):
        return ''.join(e['text'] for e in events if e['type'] == 'delta')

    def test_one_segment_streams_lines_and_matches_final(self):
        events, log, stages = self.run_summary(3, ['### 개요\n- 휴가는 15일입니다 [E2].\n- 신청은 3일 전입니다 [E1][E2].'])
        done = events[-1]['response']
        self.assertEqual(stages, ['final'])
        self.assertEqual([k for k, _ in log], ['stream'])
        self.assertEqual(len([e for e in events if e['type'] == 'delta']), 3)
        self.assertEqual(self.text_of(events), done.summary)
        self.assertEqual(done.summary, '### 개요\n- 휴가는 15일입니다 [1].\n- 신청은 3일 전입니다 [2][1].')
        self.assertEqual([(c.number, c.location_label) for c in done.citations], [(1, 'p.2'), (2, 'p.1')])
        self.assertTrue(done.is_full)
        self.assertEqual(done.cited_ratio, 0.667)

    def test_two_segments_rolling_serial(self):
        events, log, stages = self.run_summary(5, ['- 앞 구간입니다 [E1][E2].', '- 앞 [E1]. 뒤 [E4].'], limit=300)
        self.assertEqual(stages, ['step', 'final'])
        self.assertEqual([k for k, _ in log], ['invoke', 'stream'])
        self.assertIn('이전 요약:\n- 앞 구간입니다 [E1][E2].', log[1][1])
        self.assertTrue(events[-1]['response'].is_full)

    def test_balanced_split_avoids_needless_exclusion(self):
        # 100토큰 청크 5개, 단계 한도 250: 2구간이면 넘침 -> 3구간으로 전부 처리
        events, log, stages = self.run_summary(5, ['- a [E1].', '- b [E1][E3].', '- c [E1][E5].'], limit=250)
        self.assertEqual(stages, ['step', 'step', 'final'])
        self.assertTrue(events[-1]['response'].is_full)

    def test_over_three_segments_excludes_tail(self):
        events, _, stages = self.run_summary(10, ['- a [E1].', '- b [E1][E3].', '- c [E1][E5].'], limit=200)
        response = events[-1]['response']
        self.assertEqual(len(stages), 3)
        self.assertFalse(response.is_full)
        self.assertEqual(response.excluded_range, '예산 초과로 제외 p.7~p.10')

    def test_uncited_line_is_never_shown(self):
        events, _, _ = self.run_summary(2, ['- 근거 있음 [E1].\n- 근거 없는 문장입니다.\n- 다음 [E2].', '- 다시 [E1].'])
        shown = [e['text'] for e in events if e['type'] == 'delta']
        self.assertNotIn('근거 없는', ''.join(shown))
        self.assertIn({'type': 'reset', 'reason': '근거 확인에 실패해 다시 생성합니다.'}, events)
        self.assertEqual(events[-1]['response'].summary, '- 다시 [1].')

    def test_retry_limited_by_three_calls(self):
        bad = '- 근거 없는 문장입니다.'
        events, log, _ = self.run_summary(5, ['- a [E1].', '- b [E1][E3].', bad], limit=250)
        self.assertEqual(len(log), 3)
        self.assertEqual(events[-1], {'type': 'error', 'code': 'INVALID_CITATION'})

    def test_middle_failure_keeps_chain_and_marks_excluded(self):
        events, log, _ = self.run_summary(5, ['- 근거 없는 문장입니다.', '- 다시 실패입니다.', '- 뒤 [E4].'], limit=300)
        response = events[-1]['response']
        self.assertEqual(len(log), 3)
        self.assertFalse(response.is_full)
        self.assertEqual(response.excluded_range, '근거 확인 실패로 제외 p.1~p.3')

    def test_unknown_alias_line_blocked(self):
        events, _, _ = self.run_summary(2, ['- 가 [E99].', '- 다시 [E1].'])
        self.assertNotIn('[E99]', self.text_of(events))
        self.assertEqual(events[-1]['response'].summary, '- 다시 [1].')

    def test_stream_rejected_falls_back_to_invoke(self):
        error = BadRequestError('Your organization must be verified to stream this model.')
        events, log, _ = self.run_summary(2, ['- 가 [E1].'], stream_error=error)
        self.assertEqual([k for k, _ in log], ['invoke'])
        self.assertEqual(events[-1]['response'].summary, '- 가 [1].')

    def test_context_overflow_replans_once(self):
        events, log, stages = self.run_summary(2, ['- 가 [E1].'], overflow_first=True)
        self.assertEqual(stages, ['final', 'final'])
        self.assertEqual(events[-1]['response'].summary, '- 가 [1].')

    def test_heading_rule_for_ask_too(self):
        for line in ('### 개요', '**중요**', '### 필요 서류'):
            cit.validate_citations_line(line)
        with self.assertRaises(ChatbotError):
            cit.validate_citations_line('**기한은 6 업무시간입니다**')

    def test_wrapper_returns_response(self):
        with ExitStack() as stack:
            stack.enter_context(patch('hyundap.embedding.vector_store.get_document_chunks', return_value=[vhit(0)]))
            stack.enter_context(patch('hyundap.auth.permissions.verify_documents_access'))
            stack.enter_context(patch.object(summary, 'build_summary_chain', return_value=FakeChain([], ['- 가 [E1].'])))
            response = summary.summarize_document('d1', {'user_id': 'u'})
        self.assertEqual(response.summary, '- 가 [1].')

    def test_slot_released_after_error(self):
        events, _, _ = self.run_summary(0, [])
        self.assertEqual(events[-1], {'type': 'error', 'code': 'NO_EVIDENCE'})
        self.assertTrue(summary._SLOT.acquire(blocking=False))
        summary._SLOT.release()



class SummaryModelTests(unittest.TestCase):
    """요약 전용 모델 설정을 확인한다 (실제 API는 부르지 않는다)."""

    def model_with(self, name):
        from types import SimpleNamespace
        from hyundap.chatbot import prompts
        prompts.get_summary_model.cache_clear()
        settings = SimpleNamespace(openai_api_key='sk-test', openai_chat_model=name)
        with patch.object(prompts, 'get_settings', return_value=settings):
            model = prompts.get_summary_model(True)
        prompts.get_summary_model.cache_clear()
        return model

    def test_gpt5_gets_reasoning_effort_and_long_timeout(self):
        model = self.model_with('gpt-5')
        self.assertEqual(model.reasoning_effort, 'low')
        self.assertEqual(model.request_timeout, 300)
        self.assertTrue(model.streaming)

    def test_other_model_has_no_reasoning_effort(self):
        self.assertIsNone(self.model_with('gpt-4o-mini').reasoning_effort)

    def test_o_series_reasoning_model_gets_effort(self):
        self.assertEqual(self.model_with('o3-mini').reasoning_effort, 'low')

    def test_other_o_prefixed_model_has_no_reasoning_effort(self):
        self.assertIsNone(self.model_with('omni-moderation-latest').reasoning_effort)

    def test_answer_model_timeout_unchanged(self):
        from types import SimpleNamespace
        from hyundap.chatbot import prompts
        prompts.get_chat_model.cache_clear()
        with patch.object(prompts, 'get_settings', return_value=SimpleNamespace(openai_api_key='sk-test', openai_chat_model='gpt-5')):
            self.assertEqual(prompts.get_chat_model().request_timeout, 30)
        prompts.get_chat_model.cache_clear()

    def test_answer_uses_gpt4o_while_summary_follows_settings(self):
        # 질문 답변만 ANSWER_MODEL(gpt-4o)로 바꾸고, 요약은 공통 설정 모델(gpt-5)을 그대로 쓴다
        from types import SimpleNamespace
        from hyundap.chatbot import prompts
        prompts.get_chat_model.cache_clear()
        prompts.get_summary_model.cache_clear()
        settings = SimpleNamespace(openai_api_key='sk-test', openai_chat_model='gpt-5')
        with patch.object(prompts, 'get_settings', return_value=settings):
            self.assertEqual(prompts.get_chat_model().model_name, 'gpt-4o')
            self.assertEqual(prompts.get_summary_model(True).model_name, 'gpt-5')
        prompts.get_chat_model.cache_clear()
        prompts.get_summary_model.cache_clear()


class PlanTokenTests(unittest.TestCase):
    """실제 tiktoken으로 센 토큰 기준 구간 계획을 확인한다."""

    def test_real_tokens_single_segment(self):
        hits = [summary.summarize_document_to_hit(vhit(i), ChunkHit) for i in range(3)]
        plan = summary.plan_summary_segments(hits, summary.step_token_limit())
        self.assertEqual(len(plan.segments), 1)
        self.assertEqual(plan.excluded, [])
        self.assertGreater(plan.total_tokens, 0)



class SummarySlotTests(unittest.IsolatedAsyncioTestCase):
    """요약 자리가 없을 때 시간 제한 안에 '진행 중' 안내로 끝나고 공용 스레드 풀을 붙잡지 않는지 확인한다."""

    def setUp(self):
        self.assertTrue(summary._SLOT.acquire(blocking=False))  # 요약 1건이 자리를 잡은 상태
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(summary, 'SUMMARY_SLOT_WAIT_SECONDS', 0.2))
        self.stack.enter_context(patch('hyundap.embedding.vector_store.get_document_chunks', return_value=[vhit(0)]))
        self.stack.enter_context(patch('hyundap.auth.permissions.verify_documents_access'))

    def tearDown(self):
        self.stack.close()
        summary._SLOT.release()

    def test_busy_slot_returns_retryable_error(self):
        started = time.monotonic()
        with self.assertRaises(ChatbotError) as caught:
            summary.summarize_document('d1', {'user_id': 'u'})
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(caught.exception.code, 'LLM_FAILED')
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.message, '다른 요약이 진행 중입니다. 잠시 후 다시 시도해 주세요.')

    def test_busy_slot_stream_sends_wait_status_first(self):
        events = summary.summarize_document_stream('d1', {'user_id': 'u'})
        self.assertEqual(next(events)['stage'], 'wait')
        with self.assertRaises(ChatbotError):
            next(events)

    async def test_waiting_summaries_do_not_starve_thread_pool(self):
        # 리뷰 재현: 기다리는 요약 12건이 asyncio 기본 스레드 풀을 채워도 다른 백엔드 호출이 곧 실행된다
        waiting = [asyncio.create_task(asyncio.to_thread(summary.summarize_document, 'd1', {'user_id': 'u'}))
                   for _ in range(12)]
        other = await asyncio.wait_for(asyncio.to_thread(lambda: 'other'), timeout=2)
        results = await asyncio.gather(*waiting, return_exceptions=True)
        self.assertEqual(other, 'other')
        self.assertTrue(all(isinstance(r, ChatbotError) and r.retryable for r in results))


if __name__ == '__main__':
    unittest.main()
