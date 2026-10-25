"""Offline regressions; no database, model API, or production data changes."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mcp_server
from evaluation import metrics, run, report, systems
from evaluation.common import read_json, write_json
from pipeline import authority, ingest, cidoc


class MetricsSafety(unittest.TestCase):
    def test_provider_failures_are_not_answers(self):
        for text in ('', '[Error: unavailable]', 'Gemini 오류 (model): unavailable',
                     'AI 응답을 생성하지 못했습니다.', 'API Key가 설정되지 않았습니다.'):
            self.assertTrue(metrics.is_generation_failure(text), text)
        self.assertFalse(metrics.is_generation_failure('자료에서 확인되지 않음'))

    def test_denial_does_not_cover_positive_fact(self):
        fact = ['유관순은 아우내 시위에 참여했다.']
        self.assertEqual(metrics.evaluate_faithfulness('유관순은 아우내 시위에 참여하지 않았다.', fact)[0], 0)
        self.assertEqual(metrics.evaluate_faithfulness(fact[0], fact)[0], 100)

    def test_unrelated_negation_does_not_hide_trap(self):
        trap = ['유관순이 독립선언서를 작성했다']
        score = lambda answer: metrics.evaluate_hallucination(answer, trap, 'test', ['유관순'])[0]
        self.assertEqual(score('유관순이 독립선언서를 작성했다. 다른 인물은 확인되지 않습니다.'), 100)
        self.assertEqual(score('유관순이 독립선언서를 작성하지 않았다.'), 0)

    def test_ranked_items_preserve_mrr(self):
        self.assertEqual(metrics.evaluate_retrieval_direct({'target_entities': ['유관순']},
                         ['무관한 기록', '유관순 기록'])['entity_mrr'], 0.5)

    def test_average_precision_is_rank_sensitive(self):
        self.assertAlmostEqual(metrics.average_precision([0, 1, 1]), (0.5 + 2 / 3) / 2)
        self.assertEqual(metrics.average_precision([1, 1, 0]), 1)
        self.assertEqual(metrics.average_precision([0, 0]), 0)

    def test_evidence_items_preserve_source_blocks_and_path_order(self):
        block = '<SOURCE_EVIDENCE id="raw_event_info-1">a\nb</SOURCE_EVIDENCE>'
        self.assertEqual(metrics.evidence_items(['path1\npath2\n' + block]), ['path1', 'path2', block])
        self.assertEqual(metrics.evidence_items(['<GRAPH_TOPOLOGY>\ntriple\n</GRAPH_TOPOLOGY>']), ['triple'])
        self.assertEqual(metrics.evidence_items(['<SOURCE_EVIDENCE id="x">cut off']), ['<SOURCE_EVIDENCE id="x">cut off'])


class McpSafety(unittest.TestCase):
    def test_sql_mutations_rejected_before_connecting(self):
        queries = ['WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x',
                   'SELECT 1; DELETE FROM t', "SELECT set_config('transaction_read_only','off',true)",
                   'SELECT 1 /* comment */', 'SELECTED 1', None]
        with patch.object(mcp_server, '_pg_connection') as connect:
            for query in queries:
                result = asyncio.run(mcp_server.handle_call_tool('query_postgres', {'query': query}))
                self.assertIn('Error:', result[0].text)
            connect.assert_not_called()

    def test_pg_readonly_timeout_and_row_limit(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchmany.return_value = [{'n': 1}] * (mcp_server.MAX_ROWS + 1)
        with patch.object(mcp_server, '_pg_connection', return_value=conn):
            result = asyncio.run(mcp_server.handle_call_tool('query_postgres', {'query': 'SELECT 1'}))
        self.assertIn('exceeds', result[0].text)
        conn.set_session.assert_called_once_with(readonly=True)
        self.assertEqual(cur.execute.call_args_list[0].args[0], "SET LOCAL statement_timeout = '15s'")
        conn.close.assert_called_once()

    def test_cypher_mutations_and_procedures_rejected(self):
        with patch.object(mcp_server, '_neo4j_driver') as driver:
            for query in ('MATCH (n) DETACH DELETE n', 'CALL apoc.cypher.run("RETURN 1", {})',
                          'RETURN 1; CREATE ()'):
                with self.assertRaises(ValueError):
                    mcp_server._read_neo4j(query)
            driver.assert_not_called()

    def test_cypher_plan_must_be_readonly(self):
        driver = MagicMock()
        tx = driver.__enter__.return_value.session.return_value.__enter__.return_value.begin_transaction.return_value.__enter__.return_value
        tx.run.return_value.consume.return_value.query_type = 'rw'
        with patch.object(mcp_server, '_neo4j_driver', return_value=driver):
            with self.assertRaises(ValueError):
                mcp_server._read_neo4j('MATCH (n) RETURN n')
        tx.run.assert_called_once_with('EXPLAIN MATCH (n) RETURN n')

    def test_build_credentials_are_not_reused(self):
        with patch.object(mcp_server, 'setting', return_value=''):
            with self.assertRaises(ValueError): mcp_server._pg_connection()
            with self.assertRaises(ValueError): mcp_server._neo4j_driver()


class DcentSafety(unittest.TestCase):
    def test_narrative_timeout_does_not_wait_on_executor_exit(self):
        pool = MagicMock()
        pool.submit.return_value.result.side_effect = TimeoutError()
        client = MagicMock(url='http://offline.invalid/docent.php')
        client.call.side_effect = [{}, {"nodes": []},
                                  {"context": "path", "context_items": ["path"], "text": "answer", "fact_table": "facts"}]
        with patch.object(systems, '_DCENT', client), \
             patch.object(systems, 'client_keywords', return_value=[]), \
             patch('concurrent.futures.ThreadPoolExecutor', return_value=pool):
            result = systems.run_dcent_e2e({'query': 'question'})
        pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        self.assertEqual(result['contexts'], ['path'])

    def test_rate_limit_cannot_extend_request_budget(self):
        client = systems.DcentClient('http://offline.invalid')
        client.token = 'offline'
        response = MagicMock(status_code=429, text='limited', headers={'Retry-After': '60'})
        with patch.object(systems.requests, 'post', return_value=response), \
             patch.object(systems.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError): client.call('narrative', {}, timeout=20)
            sleep.assert_not_called()

    def test_server_model_versions_are_recorded(self):
        client = systems.DcentClient('http://offline.invalid')
        client.token = 'offline'
        response = MagicMock(status_code=200)
        response.json.return_value = {'text': 'answer', 'model_versions': {'dated-server-model': 2}}
        with patch.object(systems.requests, 'post', return_value=response), \
             patch.object(systems, 'MODEL_VERSIONS') as versions:
            client.call('explain', {})
            versions.update.assert_called_once_with({'dated-server-model': 2})


class PipelineSafety(unittest.TestCase):
    def test_failed_source_stops_build(self):
        with patch.object(ingest, 'get_pg_engine'), patch.object(ingest, 'process_task', return_value=False) as task:
            with self.assertRaises(RuntimeError): ingest.load_sources()
            task.assert_called_once()

    def test_tei_failure_is_not_success(self):
        conn = MagicMock()
        with patch.object(ingest, 'ensure_connection', return_value=conn), \
             patch.object(ingest, 'list_tables', return_value=['raw_test']), \
             patch.object(ingest, 'build_entity_catalog', return_value={}), \
             patch.object(ingest, 'load_table_to_tei', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError): ingest.wrap_tei()
        conn.close.assert_called_once()

    def test_empty_cidoc_run_clears_old_mappings_and_ttl(self):
        generator = cidoc.CidocTimelineGenerator.__new__(cidoc.CidocTimelineGenerator)
        generator.conn = MagicMock()
        generator.cur = MagicMock()
        generator.cur.fetchall.return_value = []
        generator.output_ttl = '/unused'
        generator._ensure_mapping_table = MagicMock()
        generator.g = MagicMock()
        from rdflib import Namespace
        generator.CRM = Namespace('http://example.test/crm/')
        generator.EX = Namespace('http://example.test/data/')
        generator.generate_and_save()
        self.assertEqual(generator.cur.execute.call_args_list[0].args[0], 'DELETE FROM tei_cidoc_mappings')
        generator.conn.commit.assert_called_once()
        generator.g.serialize.assert_called_once_with(destination='/unused', format='turtle')

    def test_cidoc_failure_rolls_back_and_closes(self):
        generator = cidoc.CidocTimelineGenerator.__new__(cidoc.CidocTimelineGenerator)
        generator.conn = MagicMock()
        generator.cur = MagicMock()
        generator._generate_and_save = MagicMock(side_effect=RuntimeError('test'))
        with self.assertRaises(RuntimeError): generator.generate_and_save()
        generator.conn.rollback.assert_called_once()
        generator.conn.close.assert_called_once()
        generator.cur.close.assert_called_once()

    def test_authority_uses_shared_connection(self):
        from pipeline.config import get_pg_connection
        self.assertIs(authority.get_pg_connection, get_pg_connection)


class CheckpointSafety(unittest.TestCase):
    def test_json_handles_nonfinite_without_corrupting_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            write_json(path, {'text': 'NaN is text', 'value': float('nan')})
            self.assertEqual(read_json(path), {'text': 'NaN is text', 'value': None})
            with patch('evaluation.common.os.replace', side_effect=OSError('test')):
                with self.assertRaises(OSError): write_json(path, {'new': True})
            self.assertEqual(read_json(path)['text'], 'NaN is text')
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_fingerprint_changes_with_dataset_and_uncommitted_code(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(run, 'ROOT', Path(directory)), \
             patch.object(run, '_check_server', return_value=None), \
             patch.object(run, 'setting', side_effect=lambda key, default='': 'offline-snapshot' if key == 'EVAL_DATA_BUILD_ID' else default):
            root = Path(directory)
            (root / 'pipeline').mkdir()
            source = root / 'pipeline/code.py'
            source.write_text('x = 1')
            dataset = root / 'benchmark.json'
            dataset.write_text('[]')
            before = run._fingerprint(dataset, ['passage'])
            source.write_text('x = 2')
            self.assertNotEqual(before['code_sha256'], run._fingerprint(dataset, ['passage'])['code_sha256'])
            dataset.write_text('[{}]')
            self.assertNotEqual(before['dataset_sha256'], run._fingerprint(dataset, ['passage'])['dataset_sha256'])

    def test_judge_retries_malformed_labels_without_coercion(self):
        record = {"query_id": "q", "system": "graph", "model": "test", "user_input": "q",
                  "reference": "fact", "retrieved_contexts": ["irrelevant", "relevant"]}
        response = MagicMock()
        response.json.side_effect = [
            {"message": {"content": '{"relevant":[0.9,1]}'}},
            {"message": {"content": '{"relevant":[0,1]}'}},
        ]
        with patch.object(systems.requests, 'post', return_value=response) as post:
            result = run._judge_one(record, 'offline', 'http://offline.invalid')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result['context_precision'], 0.5)

    def test_combining_incompatible_runs_is_rejected_before_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            dirs = [Path(directory) / 'a', Path(directory) / 'b']
            for i, path in enumerate(dirs):
                write_json(path / 'config.json', {'fingerprint': {'code': str(i)}})
            with patch.object(report, '_read_scores') as scores:
                with self.assertRaises(SystemExit): report.report(dirs)
                scores.assert_not_called()

    def test_legacy_data_requires_explicit_snapshot_id(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(run, 'ROOT', Path(directory)), \
             patch.object(run, 'setting', return_value=''):
            with self.assertRaises(SystemExit): run._fingerprint(Path(directory) / 'unused', ['passage'])

    def test_incomplete_build_blocks_evaluation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(run, 'ROOT', Path(directory)):
            root = Path(directory)
            write_json(root / 'data/output/build_manifest.json', {'complete': False})
            with self.assertRaises(SystemExit): run._fingerprint(root / 'unused', ['passage'])


if __name__ == '__main__':
    with contextlib.redirect_stdout(io.StringIO()):
        unittest.main()
