-- 2026-10-26 채택한 DB_contract.md v1 기준이다. 실제 함수 구현은 #120~#122에서 진행한다.
-- 공유 DB에서 실행하지 않은 설계용 SQL이다. 스키마 v4 적용 후 사용한다.
-- MySQL 8.4 / SQLAlchemy text()의 이름 있는 인자 표기이며 mysql CLI 직접 실행 문법이 아니다.
-- 채택한 물리 이름: documents.id, document_versions.id, documents.doc_type.
-- 버전 필드: id, document_id, company_id, version_no, version_status,
-- data_category, file_name, sha256, expected_chunk_count를 사용한다.
-- 청크 필드: id(Chroma 청크 ID), version_id, chunk_index, content_hash.
-- 사용자 필드: password_hash, auth_version, 생성 컬럼 login_identifier.
-- 앱 역할은 소문자 표준값이다. 기존 대문자 저장값은 모듈 경계에서 매핑한다.
-- IN 목록 인자는 bindparam(name, expanding=True)로 바인딩해야 한다.
-- document_ids가 비면 []를 반환하고, allowed_company_ids가 비면 SQL 실행 전에 거부한다.
-- 권한 범위는 현재 서버 DB 상태로 판정한다. 호출자가 전달한 회사 범위를 신뢰하지 않는다.

-- Q01은 로그인 구현 범위 제외에 따라 제거했다. 후속 쿼리 번호는 유지한다.

-- Q02 회사 목록: 실행 전에 현재 사용자가 platform_admin인지 확인한다.
-- 다른 역할은 자기 회사만 조회한다. 권한 확인 실패 시 이 전체 목록 조회로 넘어가지 않는다.
SELECT id, name FROM companies WHERE is_active = 1 ORDER BY id;

-- Q03 중복 후보: 삭제·실패 버전도 포함한다. 실패한 경우 같은 후보 버전을 재시도한다.
-- 레거시 가져오기 완료 전에는 해당 회사의 새 업로드를 막는다(DB_contract.md).
-- 이 SELECT만으로 동시 중복 삽입을 막을 수 없으므로 최종 저장 제약도 필요하다.
SELECT d.id AS document_id, v.id AS version_id,
       d.deleted_at, v.version_status
FROM documents d JOIN document_versions v
  ON v.document_id = d.id AND v.company_id = d.company_id
WHERE d.company_id = :company_id AND v.sha256 = :sha256;

-- Q04 FUN-247용 문서 정보 일괄 조회. DB에 없는 ID는 결과에서 빠진다.
-- 서버 내부 후보 메타데이터 조회다. 공개·사용자 권한 검사는 Q05 또는 담당 서비스가 수행한다.
-- 기존 PK 이름을 바꾸지 않고 alias로 명세의 필드명에 맞춘다.
SELECT d.id AS document_id, d.company_id, c.name AS company_name,
       d.doc_type, d.publication_status, d.deleted_at, d.active_version_id,
       v.version_no, v.version_status, v.data_category, v.file_name
FROM documents d JOIN companies c ON c.id = d.company_id
LEFT JOIN document_versions v
  ON v.id = d.active_version_id AND v.document_id = d.id
 AND v.company_id = d.company_id
WHERE d.id IN :document_ids;

-- Q05 인용·검색 후보 한 건의 접근 조건 확인. 실제 서비스에서는 일괄 처리한다.
-- :hit_company_id는 벡터 후보의 회사, :hit_version_id는 후보·인용의 버전 ID다.
-- 최종 재검증에서는 이 SQL 외에 현재 사용자·세션·역할도 다시 확인한다.
SELECT d.id AS document_id, v.id AS version_id, c.name AS company_name,
       v.file_name, d.doc_type
FROM documents d JOIN companies c ON c.id = d.company_id
JOIN document_versions v
  ON v.id = d.active_version_id AND v.document_id = d.id
 AND v.company_id = d.company_id
WHERE d.id = :document_id
  AND d.company_id IN :allowed_company_ids
  AND d.company_id = :hit_company_id
  AND c.is_active = 1
  AND v.id = :hit_version_id
  AND d.publication_status = 'published' AND d.deleted_at IS NULL
  AND v.version_status = 'ready' AND v.data_category = 'operational';

-- Q06 FUN-318의 Chroma 실제 ID 집합과 비교할 DB 기준 청크 목록이다.
-- 개수만 맞추지 말고 청크 식별·소속·필요한 내용 검증도 수행한다.
SELECT id AS chunk_id, chunk_index, content_hash
FROM document_chunks WHERE version_id = :version_id ORDER BY chunk_index;

-- Q07 호출자가 시작한 짧은 트랜잭션 안에서 게시 후보를 잠근다.
-- 모든 게시 경로가 문서를 먼저 잠그고 후보 버전을 나중에 잠가 순서를 통일한다.
SELECT id, company_id, active_version_id, publication_status, deleted_at
FROM documents WHERE id = :document_id FOR UPDATE;
SELECT id, document_id, company_id, version_status, data_category, expected_chunk_count
FROM document_versions
WHERE id = :candidate_id AND document_id = :document_id
  AND company_id = :company_id FOR UPDATE;
-- 현재 사용자·완전성·작업 상태를 다시 확인한다. P0는 단일 순차 작업자다.
-- SQL 잠금을 유지한 채 Chroma를 호출하지 않는다.
-- 적격 조건이 바뀔 수 있다면 manifest를 확정하고 후보 세대가 그대로인지 확인한다.

-- Q08 Q07의 트랜잭션 안에서 모든 적격 조건을 검사한 뒤 활성 포인터를 조건부 전환한다.
-- MySQL <=>는 NULL도 비교하는 동등 연산자다. 바뀐 행이 정확히 1건인지 확인한다.
-- 후보가 이미 활성 상태면 같은 요청의 반복 성공으로 별도 처리한다.
-- Chroma 상태까지 원자적으로 바꾸지는 못한다. 별도 활성화·실패 복구를 연결한다.
UPDATE documents SET active_version_id = :candidate_id
WHERE id = :document_id AND company_id = :company_id
  AND deleted_at IS NULL AND active_version_id <=> :expected_active_id
  AND publication_status IN ('draft', 'published')
  AND EXISTS (
    SELECT 1 FROM document_versions v
    WHERE v.id = :candidate_id AND v.document_id = documents.id
      AND v.company_id = documents.company_id
      AND v.version_status = 'ready' AND v.data_category = 'operational'
  );
-- 변경 기록을 남기는 경우 작업 상태와 같은 트랜잭션에 기록한다.

-- Q09 P3 참고용이며 P0 구현 대상이 아니다. 호출자는 사용자·대상 계정을 잠근다.
-- 회사 범위·허용 역할 변경을 검사하고 필요한 변경 기록을 같은 트랜잭션에 남긴다.
-- auth_version 증가는 세션 검사 코드가 새 값을 확인해야 실제 권한 무효화로 이어진다.
UPDATE users SET role = :new_role, auth_version = auth_version + 1
WHERE id = :target_user_id AND auth_version = :expected_auth_version;

-- Q10 논리 삭제. :deleted_at은 계약한 시각 타입과 일치해야 한다.
-- 호출자가 권한·변경 기록을 처리하고 커밋 후 원본·벡터 정리를 요청한다.
UPDATE documents SET deleted_at = :deleted_at, publication_status = 'blocked'
WHERE id = :document_id AND company_id IN :allowed_company_ids
  AND deleted_at IS NULL;

-- Q11 공개 문서 정합성 점검. 정상 상태라면 결과가 0행이어야 한다.
SELECT d.id, d.company_id, d.active_version_id
FROM documents d LEFT JOIN document_versions v
  ON v.id = d.active_version_id AND v.document_id = d.id
 AND v.company_id = d.company_id
WHERE d.publication_status = 'published' AND d.deleted_at IS NULL
  AND (v.id IS NULL OR v.version_status <> 'ready'
       OR v.data_category <> 'operational');

-- Q12 DB 청크 목록 개수 점검. DB와 Chroma의 실제 ID 집합 비교를 대체하지 않는다.
SELECT v.id, v.expected_chunk_count, COUNT(ch.id) AS manifest_count
FROM document_versions v LEFT JOIN document_chunks ch ON ch.version_id = v.id
WHERE v.version_status = 'ready'
GROUP BY v.id, v.expected_chunk_count
HAVING v.expected_chunk_count IS NULL OR v.expected_chunk_count <= 0
    OR COUNT(ch.id) <> v.expected_chunk_count;

-- 생성·보고서 INSERT 함수는 #121에서 구현한다. 채택한 자료형·DDL은 계약 문서에 있다.
-- #118/#119에서 광범위한 감사 하위 시스템을 추가하지 않는다.
-- 스키마 v3에 이 SQL을 그대로 적용하지 않는다.
