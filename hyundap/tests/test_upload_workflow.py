"""격리된 MySQL과 가짜 벡터 저장소로 업로드·게시 계약을 검증한다.

DB_WORKFLOW_TEST_URL은 127.0.0.1의 빈 hyundap_workflow_test DB만 허용한다.
실제 문서 수집기와 외부 임베딩 API는 호출하지 않는다.
"""

import hashlib
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from hyundap.auth.errors import AccessError
from hyundap.common.schemas import Chunk
from hyundap.db import chunks_repo, documents_repo, runtime, schema
from hyundap.db.errors import DbError
from .test_schema_migration import BASELINE, execute_script
from .test_db_runtime import session_fixture


class FakeEmbedding:
    """벡터 본문·소속·활성 버전을 메모리에 보관하는 외부 의존성 대역이다."""

    def __init__(self):
        """각 테스트가 독립된 저장소와 장애 주입 지점을 갖게 한다."""
        self.rows = {}
        self.active = {}
        self.activation_calls = []
        self.fail_activation = set()
        self.fail_delete = False
        self.partial = False
        self.on_embed = None
        self.on_activate = None

    def embed_and_store(self, chunks):
        """청크 본문과 식별자를 저장하고 실제 저장 건수를 보고한다."""
        if self.on_embed:
            self.on_embed()
        selected = chunks[:-1] if self.partial else chunks
        for chunk in selected:
            self.rows[chunk.chunk_id] = {
                'chunk_id': chunk.chunk_id, 'text': chunk.text,
                'metadata': {'document_id': chunk.document_id,
                             'version_id': chunk.version_id, 'company_id': chunk.company_id},
            }
        return {'status': 'success', 'total': len(chunks), 'embedded': len(selected),
                'stored': len(selected), 'skipped_existing': 0, 'failures': []}

    def get_version_chunk_ids(self, document_id, version_id):
        """지정 문서·버전에 저장된 청크 식별자를 반환한다."""
        return [row['chunk_id'] for row in self.get_document_chunks(document_id, version_id)]

    def get_document_chunks(self, document_id, version_id, active_only=False):
        """독립 복사본을 반환하여 호출자가 대역의 내부 상태를 바꾸지 못하게 한다."""
        if active_only and self.active.get(document_id) != version_id:
            return []
        return deepcopy([row for row in self.rows.values()
                         if row['metadata']['document_id'] == document_id
                         and row['metadata']['version_id'] == version_id])

    def activate_version(self, document_id, version_id):
        """활성 전환을 기록하고 지정된 버전에 한해서 장애를 발생시킨다."""
        self.activation_calls.append(version_id)
        if self.on_activate:
            self.on_activate()
        if version_id in self.fail_activation:
            raise RuntimeError('synthetic activation failure')
        self.active[document_id] = version_id
        return len(self.get_version_chunk_ids(document_id, version_id))

    def delete_vectors(self, document_id, version_id):
        """후보 버전 벡터를 삭제하거나 복구 장애를 재현한다."""
        if self.fail_delete:
            raise RuntimeError('synthetic delete failure')
        ids = self.get_version_chunk_ids(document_id, version_id)
        for chunk_id in ids:
            del self.rows[chunk_id]
        return len(ids)


@unittest.skipUnless(os.getenv('DB_WORKFLOW_TEST_URL'), '격리 MySQL URL 필요')
class UploadWorkflowTests(unittest.TestCase):
    """실제 DB 상태와 외부 저장소 상태를 함께 비교한다."""

    @classmethod
    def setUpClass(cls):
        """빈 전용 DB에 기존 기준 스키마와 추가 마이그레이션을 적용한다."""
        url = make_url(os.environ['DB_WORKFLOW_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_workflow_test':
            raise RuntimeError('로컬 전용 테스트 DB 이름을 확인하세요.')
        cls.engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                                   connect_args={'init_command': "SET time_zone='+00:00'"})
        cls.addClassCleanup(cls.engine.dispose)
        with cls.engine.begin() as database:
            if inspect(database).get_table_names():
                raise RuntimeError('기존 테이블이 있는 DB에는 실행하지 않습니다.')
            execute_script(database, BASELINE)
            execute_script(database, schema.prepare_sql(schema.snapshot(database)))
            database.execute(text('UPDATE schema_migrations SET version=4 WHERE version=3'))

    def setUp(self):
        """테스트별 회사·관리자·원본 파일·벡터 저장소를 준비한다."""
        from hyundap.db import upload_service, version_service
        self.upload_service, self.version_service = upload_service, version_service
        engine_patch = patch.object(runtime, 'get_engine', return_value=self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.company, self.other_company, self.user_id = [str(uuid4()) for _ in range(3)]
        email = f'{self.user_id}@test.invalid'
        with self.engine.begin() as database:
            database.execute(text('INSERT INTO companies (id,name) VALUES (:id,:name)'),
                             [{'id': self.company, 'name': '합성 회사'},
                              {'id': self.other_company, 'name': '다른 합성 회사'}])
            database.execute(text('''INSERT INTO users
                (id,company_id,email,role)
                VALUES (:id,:company,:email,'COMPANY_ADMIN')'''),
                {'id': self.user_id, 'company': self.company, 'email': email})
        self.actor = session_fixture(self.engine, self.user_id, self.company)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / '원본.txt'
        self.source.write_text('합성 원본 ' + str(uuid4()), encoding='utf-8')
        self.upload_dir = self.directory / 'uploads'
        self.embedding = FakeEmbedding()

    def ingest(self, path, upload_info):
        """업로드 식별자를 그대로 반영한 두 개의 공통 청크를 만든다."""
        chunks = []
        for index in range(2):
            chunks.append(Chunk(
                chunk_id=f"{upload_info['version_id']}:{index:05d}",
                document_id=upload_info['document_id'], version_id=upload_info['version_id'],
                company_id=upload_info['company_id'], company_name='합성 회사', doc_type='manual',
                file_name=Path(path).name, section_no=1, location_label='p.1',
                chunk_index=index, text=f'합성 본문 {index}'))
        return {'status': 'success', 'chunks': chunks, 'report': {'status': 'success'}}

    def upload(self, **metadata):
        """정상 의존성을 주입하여 업로드 진입점을 호출한다."""
        return self.upload_service.upload_document(
            self.actor, self.source, dict(company_id=self.company, doc_type='manual', **metadata),
            ingest_document=self.ingest, embedding=self.embedding, upload_dir=self.upload_dir)

    def active_version(self, document_id):
        """DB의 실제 활성 포인터를 조회한다."""
        with self.engine.connect() as database:
            return database.execute(text('SELECT active_version_id FROM documents WHERE id=:id'),
                                    {'id': document_id}).scalar_one()

    def candidate(self, document_id=None):
        """게시 검증만 시험할 수 있도록 준비된 비활성 후보를 만든다."""
        metadata = {'company_id': self.company, 'doc_type': 'manual', 'file_name': '후보.txt',
                    'storage_path': 'synthetic/candidate.txt', 'sha256': uuid4().hex * 2,
                    'file_size': 10, 'data_category': 'operational'}
        if document_id:
            metadata['document_id'] = document_id
        result = documents_repo.create_document_version(self.actor, metadata)
        chunks = self.ingest(self.source, dict(result, company_id=self.company))['chunks']
        chunks_repo.save_manifest(result['version_id'], [
            {'id': chunk.chunk_id, 'chunk_index': chunk.chunk_index,
             'content_hash': hashlib.sha256(chunk.text.encode()).hexdigest()} for chunk in chunks])
        self.embedding.embed_and_store(chunks)
        with self.engine.begin() as database:
            database.execute(text("UPDATE document_versions SET version_status='ready' WHERE id=:id"),
                             {'id': result['version_id']})
        return result

    def publish(self, candidate, expected=None):
        """후보를 기대 활성 포인터와 함께 게시한다."""
        return self.version_service.publish_version(
            self.actor, candidate['document_id'], candidate['version_id'], expected,
            embedding=self.embedding)

    def test_upload_saves_original_and_publishes_complete_version(self):
        """원본 저장·전체 청크 검증·게시가 하나의 사용자 흐름으로 완료된다."""
        result = self.upload()
        self.assertEqual(self.active_version(result['document_id']), result['version_id'])
        with self.engine.connect() as database:
            row = database.execute(text('SELECT storage_path,sha256,file_size,version_status '
                                        'FROM document_versions WHERE id=:id'),
                                   {'id': result['version_id']}).mappings().one()
        saved = Path(row['storage_path'])
        if not saved.is_absolute():
            saved = self.upload_dir / saved
        self.assertTrue(saved.resolve().is_relative_to(self.upload_dir.resolve()))
        self.assertEqual(saved.read_bytes(), self.source.read_bytes())
        self.assertEqual(row['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(row['file_size'], self.source.stat().st_size)
        self.assertEqual(row['version_status'], 'ready')

    def test_ingest_failure_preserves_published_version(self):
        """수집 실패가 기존 게시 포인터와 검색 벡터에 영향을 주지 않는다."""
        previous = self.upload()
        self.source.write_text('변경된 원본', encoding='utf-8')
        with patch.object(self, 'ingest', return_value={'status': 'failed', 'chunks': [],
                                                       'error_code': 'EMPTY_DOCUMENT', 'report': {}}):
            failed = self.upload(document_id=previous['document_id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.active_version(previous['document_id']), previous['version_id'])
        self.assertEqual(self.embedding.active[previous['document_id']], previous['version_id'])

    def test_partial_embedding_never_replaces_active_version(self):
        """일부 청크만 저장되면 기존 활성 버전이 유지된다."""
        previous = self.upload()
        self.source.write_text('다른 변경 원본', encoding='utf-8')
        self.embedding.partial = True
        failed = self.upload(document_id=previous['document_id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.active_version(previous['document_id']), previous['version_id'])

    def test_failed_upload_retries_saved_original_with_same_identifiers(self):
        """실패 후 원본을 보존하고 같은 버전·작업 식별자로 처리를 재시도한다."""
        self.embedding.partial = True
        failed = self.upload()
        self.assertEqual(failed['status'], 'failed')
        self.source.unlink()
        self.embedding.partial = False
        retried = self.upload_service.retry_upload(
            self.actor, failed['version_id'], ingest_document=self.ingest, embedding=self.embedding)
        for key in ('document_id', 'version_id', 'job_id'):
            self.assertEqual(retried[key], failed[key])
        self.assertEqual(self.active_version(failed['document_id']), failed['version_id'])

    def test_dataclass_ingest_preserves_chunk_objects_for_embedding(self):
        """수집 결과가 데이터클래스여도 내부 공통 청크 객체를 임베더에 전달한다."""
        @dataclass
        class IngestResult:
            """팀 간 수집 반환 계약의 최소 필드를 재현한다."""
            status: str
            chunks: list[Chunk]
            report: dict

        def ingest_dataclass(path, info):
            """일반 수집 결과를 데이터클래스 결과로 감싼다."""
            return IngestResult(**self.ingest(path, info))

        result = self.upload_service.upload_document(
            self.actor, self.source, {'company_id': self.company, 'doc_type': 'manual'},
            ingest_document=ingest_dataclass, embedding=self.embedding, upload_dir=self.upload_dir)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.active_version(result['document_id']), result['version_id'])

    def test_missing_saved_original_can_be_resupplied_for_retry(self):
        """복사 실패로 저장 원본이 없어도 같은 원본을 재공급하여 같은 후보를 완료한다."""
        with patch.object(self.upload_service, 'copy_original', side_effect=OSError('synthetic copy failure')):
            failed = self.upload()
        self.assertEqual(failed['status'], 'failed')
        retried = self.upload_service.retry_upload(
            self.actor, failed['version_id'], source_path=self.source,
            ingest_document=self.ingest, embedding=self.embedding)
        self.assertEqual(retried['status'], 'success')
        self.assertEqual(retried['version_id'], failed['version_id'])
        self.assertEqual(retried['job_id'], failed['job_id'])

    def test_external_runtime_error_is_sanitized_in_response_and_job(self):
        """외부 수집기의 예상 밖 예외 원문이 반환값과 작업 원장에 노출되지 않는다."""
        secret = 'synthetic-private-external-value'

        def failing_ingest(path, info):
            """비밀 문자열을 포함한 외부 모듈 예외를 발생시킨다."""
            raise RuntimeError(secret)

        result = self.upload_service.upload_document(
            self.actor, self.source, {'company_id': self.company, 'doc_type': 'manual'},
            ingest_document=failing_ingest, embedding=self.embedding, upload_dir=self.upload_dir)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn(secret, json.dumps(result))
        with self.engine.connect() as database:
            row = database.execute(text('SELECT report,error_code,error_message FROM index_jobs WHERE id=:id'),
                                   {'id': result['job_id']}).mappings().one()
        self.assertNotIn(secret, str(dict(row)))

    def test_completeness_rejects_missing_unexpected_duplicate_and_tampered_chunks(self):
        """청크 집합·중복·회사·본문 해시가 원장과 다른 경우 검증을 거부한다."""
        candidate = self.candidate()
        baseline = deepcopy(self.embedding.rows)
        first = next(iter(baseline))
        for mutation in ('missing', 'unexpected', 'duplicate', 'company', 'text'):
            with self.subTest(mutation=mutation):
                self.embedding.rows = deepcopy(baseline)
                if mutation == 'missing':
                    del self.embedding.rows[first]
                elif mutation == 'unexpected':
                    extra = deepcopy(baseline[first])
                    extra['chunk_id'] = candidate['version_id'] + ':99999'
                    self.embedding.rows[extra['chunk_id']] = extra
                elif mutation == 'company':
                    self.embedding.rows[first]['metadata']['company_id'] = self.other_company
                elif mutation == 'text':
                    self.embedding.rows[first]['text'] = '변조 본문'
                if mutation == 'duplicate':
                    ids = self.embedding.get_version_chunk_ids(candidate['document_id'], candidate['version_id'])
                    with patch.object(self.embedding, 'get_version_chunk_ids', return_value=ids + ids[:1]):
                        report = self.version_service.validate_completeness(
                            candidate['document_id'], candidate['version_id'], embedding=self.embedding)
                else:
                    report = self.version_service.validate_completeness(
                        candidate['document_id'], candidate['version_id'], embedding=self.embedding)
                self.assertFalse(report['ok'])

    def test_activation_failure_restores_old_pointer_and_vectors(self):
        """외부 활성화 실패 후 이전 포인터와 벡터를 복구하고 후보 벡터를 지운다."""
        previous = self.upload()
        candidate = self.candidate(previous['document_id'])
        self.embedding.fail_activation.add(candidate['version_id'])
        with self.assertRaises(DbError):
            self.publish(candidate, previous['version_id'])
        self.assertEqual(self.active_version(previous['document_id']), previous['version_id'])
        self.assertEqual(self.embedding.active[previous['document_id']], previous['version_id'])
        self.assertEqual(self.embedding.get_version_chunk_ids(
            candidate['document_id'], candidate['version_id']), [])

    def test_recovery_retries_failed_vector_cleanup(self):
        """복구 중 벡터 삭제 장애가 나도 복구 진입점에서 재시도할 수 있다."""
        previous = self.upload()
        candidate = self.candidate(previous['document_id'])
        self.embedding.fail_activation.add(candidate['version_id'])
        self.embedding.fail_delete = True
        with self.assertRaises(DbError):
            self.publish(candidate, previous['version_id'])
        self.embedding.fail_delete = False
        report = self.version_service.recover_publication(
            candidate['document_id'], candidate['version_id'], embedding=self.embedding)
        self.assertEqual(report['phase'], 'restored')
        self.assertEqual(self.active_version(previous['document_id']), previous['version_id'])

    def test_same_candidate_publication_is_idempotent(self):
        """이미 활성인 후보를 재요청해도 포인터와 벡터가 유지된다."""
        candidate = self.candidate()
        self.publish(candidate)
        self.publish(candidate)
        self.assertEqual(self.active_version(candidate['document_id']), candidate['version_id'])

    def test_concurrent_publication_has_one_compare_and_swap_winner(self):
        """같은 이전 포인터를 기준으로 경쟁한 후보 가운데 하나만 게시된다."""
        previous = self.upload()
        candidates = [self.candidate(previous['document_id']) for _ in range(2)]
        barrier = Barrier(2)

        def publish_candidate(candidate):
            """두 작업을 같은 시점에 시작하고 안전한 결과 코드만 반환한다."""
            barrier.wait(timeout=10)
            try:
                self.publish(candidate, previous['version_id'])
                return 'success'
            except DbError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(publish_candidate, candidates))
        self.assertCountEqual(results, ['success', 'CONFLICT'])

    def test_external_activation_does_not_hold_document_row_lock(self):
        """외부 호출 중 별도 SQL 연결이 문서 행을 잠글 수 있다."""
        candidate = self.candidate()

        def acquire_document_lock():
            """짧은 잠금 제한으로 DB 행 잠금이 외부 호출까지 이어지지 않는지 확인한다."""
            with self.engine.begin() as database:
                database.execute(text('SET innodb_lock_wait_timeout=1'))
                database.execute(text('SELECT id FROM documents WHERE id=:id FOR UPDATE'),
                                 {'id': candidate['document_id']}).scalar_one()

        self.embedding.on_activate = acquire_document_lock
        self.publish(candidate)

    def test_unauthorized_upload_does_not_create_files(self):
        """타 회사 업로드를 거부하며 저장 폴더 밖이나 안에 원본을 생성하지 않는다."""
        with self.assertRaises(AccessError):
            self.upload_service.upload_document(
                self.actor, self.source, {'company_id': self.other_company, 'doc_type': 'manual'},
                ingest_document=self.ingest, embedding=self.embedding, upload_dir=self.upload_dir)
        self.assertFalse(self.upload_dir.exists() and list(self.upload_dir.rglob('*')))

    def test_platform_admin_cannot_publish_inactive_company_or_query_vectors(self):
        """플랫폼 관리자도 비활성 회사에는 외부 벡터 조회 전에 게시를 거절한다."""
        candidate = self.candidate()
        with self.engine.begin() as database:
            database.execute(text("UPDATE users SET role='SUPER_ADMIN',company_id=:company WHERE id=:id"),
                             {'company': self.other_company, 'id': self.user_id})
            database.execute(text('UPDATE companies SET is_active=0 WHERE id=:id'), {'id': self.company})
        with patch.object(self.embedding, 'get_version_chunk_ids') as ids_query, \
                patch.object(self.embedding, 'get_document_chunks') as chunks_query:
            with self.assertRaises(AccessError):
                self.publish(candidate)
        ids_query.assert_not_called()
        chunks_query.assert_not_called()

    def test_blocked_or_deleted_recovery_retries_cleanup_without_superseding(self):
        """차단·삭제 상태에서 복구 정리 실패를 재시도해도 상태를 보존하고 완료한다."""
        for state in ('blocked', 'deleted'):
            with self.subTest(state=state):
                self.source.write_text('독립 원본 ' + str(uuid4()), encoding='utf-8')
                previous = self.upload()
                candidate = self.candidate(previous['document_id'])

                def restrict_document():
                    """외부 활성화 시점에 문서를 차단하거나 삭제한다."""
                    with self.engine.begin() as database:
                        if state == 'blocked':
                            database.execute(text("UPDATE documents SET publication_status='blocked' WHERE id=:id"),
                                             {'id': candidate['document_id']})
                        else:
                            database.execute(text('UPDATE documents SET deleted_at=UTC_TIMESTAMP(6) WHERE id=:id'),
                                             {'id': candidate['document_id']})

                self.embedding.on_activate = restrict_document
                self.embedding.fail_activation.add(candidate['version_id'])
                self.embedding.fail_delete = True
                with self.assertRaises(DbError):
                    self.publish(candidate, previous['version_id'])
                self.embedding.on_activate = None
                self.embedding.fail_delete = False
                report = self.version_service.recover_publication(
                    candidate['document_id'], candidate['version_id'], embedding=self.embedding)
                self.assertEqual(report['phase'], 'restored')
                with self.engine.connect() as database:
                    document = database.execute(text('SELECT publication_status,deleted_at,active_version_id '
                                                     'FROM documents WHERE id=:id'),
                                                {'id': candidate['document_id']}).mappings().one()
                    raw = database.execute(text('SELECT report FROM index_jobs WHERE id=:id'),
                                           {'id': candidate['job_id']}).scalar_one()
                reports = json.loads(raw) if isinstance(raw, str) else raw
                self.assertIsNone(reports['_publication']['restore_target_id'])
                self.assertIsNone(document['active_version_id'])
                self.assertEqual(document['publication_status'], 'blocked')
                if state == 'deleted':
                    self.assertIsNotNone(document['deleted_at'])

    def test_permission_change_during_embedding_prevents_publication(self):
        """임베딩 중 사용자가 비활성화되면 게시 직전에 다시 권한을 확인한다."""
        def deactivate_user():
            """외부 호출 중 발생하는 사용자 상태 변경을 재현한다."""
            with self.engine.begin() as database:
                database.execute(text('UPDATE users SET is_active=0 WHERE id=:id'), {'id': self.user_id})

        self.embedding.on_embed = deactivate_user
        try:
            self.upload()
        except (AccessError, DbError):
            pass
        with self.engine.connect() as database:
            active = database.execute(text('SELECT active_version_id FROM documents WHERE company_id=:id'),
                                      {'id': self.company}).scalars().all()
        self.assertTrue(all(version is None for version in active))

    def test_manifest_change_during_external_verification_prevents_publication(self):
        """외부 검증 도중 원장 해시가 바뀌면 오래된 검증 결과로 게시하지 않는다."""
        candidate = self.candidate()
        original = self.embedding.get_document_chunks
        changed = False

        def change_manifest(document_id, version_id, active_only=False):
            """벡터 조회 직후 다른 작업이 원장을 수정한 상황을 재현한다."""
            nonlocal changed
            rows = original(document_id, version_id, active_only)
            if not changed:
                changed = True
                with self.engine.begin() as database:
                    database.execute(text('UPDATE document_chunks SET content_hash=:hash '
                                          'WHERE version_id=:id AND chunk_index=0'),
                                     {'id': version_id, 'hash': 'f' * 64})
            return rows

        self.embedding.get_document_chunks = change_manifest
        with self.assertRaises(DbError):
            self.publish(candidate)
        self.assertIsNone(self.active_version(candidate['document_id']))
