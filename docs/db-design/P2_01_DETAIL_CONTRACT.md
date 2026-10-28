# DB P2-01 상세 조회 계약 — #203

## 구현 범위

2026-10-28, `db-p2-01` 브랜치. `git pull origin develop`은 Already up to date로
완료되었다. 변경 기준 HEAD는 `215a709`이며 아래 구현은 작업 트리의 미커밋 변경이다.

`hyundap/db/documents_repo.py`에 상세 전용 메인 함수 `get_document_detail`과
보조 함수 `_check_detail_access`를 추가했다. 일반 함수·dict·명시적인 조건문과
단계별 한국어 주석을 사용한다. 새 클래스, 중첩 반복문, 동적 SQL은 추가하지 않는다.

## 호출 규격

`get_document_detail(actor, document_id, *, version_id=None, connection=None)`

| 항목 | 계약 |
|---|---|
| actor | 서버 세션 ID와 토큰을 가진 문맥. 역할·회사 주장은 신뢰하지 않고 DB에서 확인 |
| document_id | 기존 ID 검사 규칙을 따르는 필수 문자열 |
| version_id | 생략하면 활성 버전, 지정하면 현재 활성 버전과 일치해야 함 |
| connection | 생략 시 자체 짧은 TX, 전달 시 호출자 TX 참여·커밋/종료 없음 |
| 반환 | document_id, company_id, company_name, version_id, file_name, doc_type, version_no, size_bytes, indexed_at의 dict |
| 크기 | 활성 버전 file_size, 바이트 단위 |
| 시각 | indexed_at=None. 후보 생성/작업 종료 시각을 색인 완료 시각으로 대체하지 않음 |
| 공개 대상 | 허용 회사의 published·미삭제 문서, ready·operational 활성 버전 |

잘못된 입력은 `DbError(INVALID_INPUT)`, DB 장애는 `DbError(DB_UNAVAILABLE)`이다.
세션 무효는 `AccessError(SESSION_EXPIRED)`, 회사 범위 위반은 `ACCESS_DENIED`,
없는 문서·공개 상태/버전 불일치는 `STATE_CHANGED`다. 회사 위반은 문서 상태 검사보다
먼저 거부한다. 경로·토큰·원시 DB 오류·본문은 반환하지 않는다.

## 기존 계약 보존과 UI 전달 사항

검색용 `get_documents_with_active_version`의 11필드 계약은 그대로다.
관리자 이력 조회와 업로드·게시·복구 함수도 변경하지 않았다.

UI 담당은 기존 상세 어댑터의 DB 조회를 이 함수로 연결할 때 반환된 `version_id`를
본문 조회에 사용하고 `size_bytes`를 화면 응답에 전달할 수 있다. `indexed_at=None`은
정보 미제공으로 처리한다. 기존 `verify_documents_access`의 조회 전후 검증을 제거하지
않는다. 외부 본문/요약 처리 후에는 새 DB 조회로 같은 문서·버전을 다시 확인해야 한다.
전달받은 긴 트랜잭션의 스냅샷은 외부 처리 완료 시점의 최신 권한 보장이 아니다.

이번 작업은 DB 함수와 계약 제공까지다. UI 변경·다운로드·미리보기·P3 관리 기능은
포함하지 않는다. UI의 기존 size_bytes/indexed_at=None은 연결 작업 전까지 유지된다.

## 검증 구성

`test_access_queries`에 5개 테스트를 추가했다. 세 역할·위조 actor·세션·회사·상태·버전·
필드 허용 목록·입력 검사·DB 오류·후속 접근 재검증을 합성 SQLite에서 검사한다.
기존 검색 11필드 및 UI 상세 어댑터 회귀를 함께 실행한다.

`test_db_runtime`에는 최종 v4 테이블에서 상세 조회와 호출자 트랜잭션 롤백을 확인하는
MySQL 테스트 1개를 추가했다. 테스트 준비 독스트링의 폐기된 v3 이행 설명도 수정했다.

전체 검증은 localhost에 새 MySQL 8.4.13 인스턴스와 빈 테스트 DB 5개를 생성하여
`python -m unittest discover -s hyundap/tests -t .`로 수행한다. 테스트 프로세스에만
DB_RUNTIME_TEST_URL, DB_CONTRACT_TEST_URL, DB_AUTH_TEST_URL, DB_WORKFLOW_TEST_URL,
DB_SCHEMA_TEST_URL을 지정한다. 외부 API/Chroma는 기존 테스트 대역을 사용한다.
Railway 접속·변경은 수행하지 않는다.

초기 전체 실행은 docx 패키지 누락으로 수집 테스트 모듈 2개를 불러오지 못했다.
임시 PYTHONPATH 경로에 requirements의 python-docx 1.2.0, python-pptx 1.0.2,
pypdf 6.21.2 및 필요한 lxml/XlsxWriter를 설치했다. Pillow는 기존 12.3.0을 유지한다.
저장소 requirements와 사용자 전역 패키지는 변경하지 않았다.

최종 결과: 전체 **375개 통과, 실패 0·오류 0·건너뜀 0**, 테스트 실행 29.045초.
별도 선행 검증은 접근/저장/UI 상세 관련 38개 통과다. 새 테스트는 총 6개다.
Python 컴파일, `git diff --check`, 변경 코드의 한자·깨진 문자 검사도 통과했다.
검증 후 이번 작업에서 시작한 격리 MySQL 프로세스를 종료했다.
