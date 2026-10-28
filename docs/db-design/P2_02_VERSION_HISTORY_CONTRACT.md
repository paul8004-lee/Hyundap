# DB P2-02 버전 이력 조회 — #204

## 기준과 변경 이유

2026-10-28, `feature/db`의 `d22d742`에서 분기한 `db-p2-02` 작업이다.
기존 이력 저장·조회는 구현되어 있으나 활성 버전 식별 필드가 없고 일부 접근 정책의
검증 근거가 부족했다. `versions_repo.list_document_versions` 하나를 보완하고
`mark_failed`와 게시·재시도·복구 로직은 변경하지 않는다.

## 함수와 반환 계약

메인 함수: `list_document_versions(actor, document_id, *, connection=None)`.
모듈: `hyundap.db.versions_repo`. 클래스 및 신규 보조 함수는 없다.

기존 9필드 `version_id/document_id/version_no/file_name/file_size/version_status/
data_category/expected_chunk_count/created_at`을 유지하고 `is_active: bool`을 추가한다.
버전 순번 오름차순의 list[dict]이며 문서에 버전이 없으면 []다.
활성 포인터가 없으면 모든 is_active가 False다. ready 상태만으로 활성으로 판단하지 않는다.
is_active는 documents.active_version_id와의 일치 여부이고 삭제·차단 문서에서도 True일 수 있다.
이는 검색·본문·다운로드 접근 허가가 아니며 일반 상세 접근은 #203의 별도 규칙을 따른다.

1. ID 검사: 잘못된 ID는 DB 연결 전에 INVALID_INPUT.
2. 현재 세션·사용자 재검증: 세션 무효는 SESSION_EXPIRED, 직원은 ACCESS_DENIED.
3. 현재 회사 범위 확인: 회사 관리자는 자신의 활성 회사, 플랫폼 관리자는 활성 회사들.
4. 이력과 활성 포인터를 같은 SELECT로 조회하고 한 번의 for에서 행을 dict로 변환.

없는 문서와 회사 범위 위반은 ACCESS_DENIED로 유지한다. 삭제·차단·비공개 문서의
관리자 이력 조회도 기존대로 허용한다. 상태별 후보를 숨기거나 삭제하지 않는다.
내부 경로·토큰은 SELECT하지 않으며 DB 장애는 DB_UNAVAILABLE이다.

호출자 연결이 있으면 해당 트랜잭션을 사용하고 직접 커밋/종료하지 않는다.
문서·회사 복합 JOIN으로 다른 회사의 버전 연결을 막는다. 조회 결과는 트랜잭션의
조회 시점 기준이며, 이후 문서 조작 시에는 해당 명령에서 권한·상태를 다시 검사해야 한다.

## 호환성과 작성 규칙

저장소 호출부는 테스트에서만 확인했다. 기존 필드·정렬·함수 서명은 유지하며
키 집합을 고정한 외부 호출자는 추가된 is_active를 반영해야 한다.
일반 함수·명시적인 if/for·dict를 사용하고 각 단계와 반복문의 역할을 한국어로 설명했다.
범용 추상화나 새 클래스를 만들지 않았다. v4 스키마 변경, v3 복원, UI 구현은 없다.

## 검증

새 테스트 4개를 `test_db_runtime`에 추가했다. 입력/오류 테스트 1개와 격리 MySQL
테스트 3개다. 현재 활성·실패·처리중 버전의 정렬/중복/정확한 10필드·bool 타입,
삭제·차단 문서 이력, 호출자 롤백, 포인터 보존, 역할·회사·세션·없는 문서,
플랫폼 관리자와 비활성 회사 및 빈 이력을 검증한다.

전체 실행은 새 로컬 MySQL 8.4.13과 빈 DB 5개에서 다음 명령을 사용한다.

```text
python -m unittest discover -s hyundap/tests -t .
```

DB_RUNTIME_TEST_URL, DB_CONTRACT_TEST_URL, DB_AUTH_TEST_URL, DB_WORKFLOW_TEST_URL,
DB_SCHEMA_TEST_URL은 테스트 프로세스에만 지정한다. 이전 #203 검증과 동일한 임시
패키지 경로를 사용한다. Railway·외부 API에는 접속하지 않으며 Chroma 등은 대역이다.
최종 결과: **379개 통과, 실패 0·오류 0·건너뜀 0**, 테스트 실행 29.601초.
게시·재시도·복구·활성 포인터 보호 및 #203 상세 조회 회귀를 포함한다.
최초 실행의 오류 1건은 새 테스트가 플랫폼 관리자에게 회사 ID를 남겨
ck_user_company 제약을 위반한 것이며 company_id=NULL로 수정 후 전체 재검증했다.
검증 후 이번 작업의 격리 서버를 종료했다. 컴파일·git diff --check와 한글 문자 검사도 통과했다.
이 결과는 d22d742 위의 미커밋 P2-02 작업 트리 기준이며 UI 이력 화면 검증은 아니다.
