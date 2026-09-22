import json
import unittest
from copy import deepcopy

from ask_source_references import register_source, resolve_answer_sources
from insights_ask_answer import normalize_evidence, prepare_model_context
from insights_ask_context import build_ask_context
from insights_ask_semantic_context import compose_ask_context_with_semantic_evidence
from ask_history_contract import normalize_ask_history_answer, AskHistoryContractError
from test_ask_history_contract import sample_answer


class SourceReferenceTests(unittest.TestCase):
    def test_same_day_sources_resolve_only_by_exact_reference(self):
        registry = {}
        first = register_source(registry, 'entry_first')
        second = register_source(registry, 'entry_second')
        context = {'sourceSignals': [dict(first, date='2026-01-01', sourceType='typed'),
                                     dict(second, date='2026-01-01', sourceType='typed')]}
        raw = [dict(second, date='2026-01-01', sourceType='typed', paraphrase='Second source', relevance='high', sourceEntryId='entry_attacker')]
        answer = resolve_answer_sources({'evidence': normalize_evidence(raw, context=context)}, registry)
        self.assertEqual(answer['evidence'][0]['sourceEntryId'], 'entry_second')
        self.assertNotIn('sourceRef', answer['evidence'][0])

    def test_unknown_or_mismatched_reference_is_rejected(self):
        registry = {}
        reference = register_source(registry, 'entry_first')
        context = {'sourceSignals': [dict(reference, date='2026-01-01', sourceType='typed')]}
        for ref, day in [('source_' + '0'*32, '2026-01-01'), (reference['sourceRef'], '2026-01-02'), ('https://evil.test', '2026-01-01')]:
            raw = [dict(sourceRef=ref, date=day, sourceType='typed', paraphrase='test', relevance='high')]
            self.assertEqual(normalize_evidence(raw, context=context), [])

    def test_legacy_evidence_never_gets_guessed_link(self):
        evidence = {'date':'2026-01-01', 'sourceType':'typed', 'paraphrase':'Legacy', 'relevance':'high'}
        context = {'sourceSignals': [evidence]}
        answer = resolve_answer_sources({'evidence': normalize_evidence([evidence], context=context)}, {})
        self.assertNotIn('sourceEntryId', answer['evidence'][0])

    def test_semantic_reference_survives_without_entry_id_in_model(self):
        registry = {}
        context = build_ask_context([], question='What was my project?')
        retrieval = dict(semanticRetrievalVersion='1', semanticRetrievalStatus='READY', evidenceCount=1, retrievalLimit=8,
                         evidence=[dict(entryId='entry_canary', entryCreatedAt='2026-01-01T00:00:00Z', sourceType='typed', score=0.1, text='Copper Lantern')])
        composed = compose_ask_context_with_semantic_evidence(context, retrieval, source_references=registry)
        prepared = prepare_model_context(composed)
        self.assertEqual(len(registry), 1)
        self.assertNotIn('entry_canary', json.dumps(prepared))
        self.assertNotIn('entryId', json.dumps(prepared))
        self.assertIn(prepared['semanticEvidence']['items'][0]['sourceRef'], registry)

    def test_saved_answers_preserve_valid_link_and_accept_legacy(self):
        answer = sample_answer()
        legacy = normalize_ask_history_answer(answer)
        self.assertNotIn('sourceEntryId', legacy['evidence'][0])
        answer['evidence'][0]['sourceEntryId'] = 'entry_canary'
        self.assertEqual(normalize_ask_history_answer(answer)['evidence'][0]['sourceEntryId'], 'entry_canary')
        for bad in ['https://evil.test', '../entry_other', '', 7]:
            invalid = deepcopy(answer)
            invalid['evidence'][0]['sourceEntryId'] = bad
            with self.assertRaises(AskHistoryContractError):
                normalize_ask_history_answer(invalid)


if __name__ == '__main__':
    unittest.main()
