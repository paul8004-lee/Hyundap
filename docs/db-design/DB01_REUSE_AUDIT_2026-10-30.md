# DB 함수 전체 순회: 추가 재사용 개선 후보

## 패치 적용 상태

2026-10-30, `db01`에서 R1–R8을 적용했다. 아래 후보 표와 함수 목록은 적용 전 감사 근거로 보존한다.

1. R1: 게시 전에 완전한 공개 결과를 확보하고, 게시 성공 후 추가 DB 조회 없이 반환한다. 실제 MySQL 게시 직후 조회 장애를 주입하는 회귀 검사로 성공 상태와 분류 필드를 확인한다.
2. R2: UI가 `get_document_detail`을 재사용한다. 실제 파일 크기를 표시하고 본문 조회 후 권한 재검증 및 인용 개수 제한을 유지한다.
3. R3–R5: 재검증된 사용자에 대한 순수 관리자 정책, 관리자 연결 대상 검증, 호출자 연결을 사용하는 원장 조회를 공유한다. 세션 조회·트랜잭션·잠금 순서는 각 호출자에 남긴다.
4. R6–R8: 고정 v4 DDL 읽기, MySQL 숫자 오류 추출, 원본 크기 상한을 공유한다. DDL 실행 정책과 문맥별 오류 변환은 유지한다.

검증: 격리 MySQL 8.4.13에서 전체 686개 검사 통과, 건너뜀 0개, 78.978초. 문서 상세 UI 단위 검사에서 DB 상세 함수 호출·본문 후 권한 재검증·실제 파일 크기 전달을 확인했다. 실제 DB 회귀 검사에서 게시 직후 추가 조회가 불가능해도 성공 및 분류 필드가 유지되는 것을 확인했다. 관리자 연결의 생략 포트와 명시적 3306 포트는 기존대로 다른 대상으로 판정한다. `git diff --check`도 통과했다.


기준: db01 `a22328e`, 2026-10-30. Python 파일 25개(빈 공개 모듈 포함), 함수 160개(메서드·중첩 함수 포함)를 AST로 순회하고 인자·호출 관계를 추출했다. 아래 후보는 관련 구현과 실제 호출부를 대조했다. 주석을 제외한 AST 본문이 완전히 같은 함수 복제는 0건이며, 이는 부분 중복이나 계약 중복이 없다는 뜻이 아니다.

이번 작업은 정적 검토다. 운영 소스·DB 설정·데이터는 변경하지 않았고, 추가 시험으로 결함을 재현한 결과도 아니다. 기존 684개 통과 기록은 앞선 리팩토링 결과이며 아래 미적용 후보의 검증 결과가 아니다.

## 우선 적용 후보 5건

| 순서 | 대상 | 현재 중복·재사용 문제 | 권장 패치 | 반드시 유지할 검증 |
|---|---|---|---|---|
| R1 | `upload_service.run_attempt`·`upload_result` | 게시 성공 후 부가 조회가 DbError이면 ID와 성공 상태만 반환한다. 자동 업로드 UI는 유형 결정과 AI 분류 필드를 필수로 요구해 성공을 오류로 표시할 가능성이 남는다. 앞선 분류 필드 보완은 보고서를 읽을 수 있는 경우를 해결했다. | 성공 결과의 공개 필드를 수집 성공 시점에 확보하고 업로드·재시도의 공통 결과 생성에 사용한다. 게시 후 재조회 실패가 이미 확정된 결과 계약을 축소하지 않게 한다. 새 거대 결과 객체보다 필요한 필드만 먼저 고정한다. | 게시 후 조회 실패를 주입하고 자동 업로드의 UI 변환까지 검사. DB 활성 포인터·벡터를 되돌리거나 실패 정리하지 않아야 한다. |
| R2 | `documents_repo.get_document_detail` ↔ `ui.backend_documents.get_document` | DB에 권한 포함 상세 조회가 있는데 UI가 검색 행 조회·접근 검사를 조합해 상세 메타데이터를 다시 만든다. DB는 파일 크기를 반환하지만 UI는 `size_bytes=None`으로 고정한다. 다운로드는 이미 DB 상세 함수를 재사용한다. | UI가 DB 상세 계약을 읽고 청크 인용·표시 변환만 추가하게 맞춘다. UI 협업 범위가 포함된다. DB의 version_id와 기존 UI의 active_version_id 참조 차이는 어댑터에서 명시한다. | 본문 읽기 후 최종 권한 재검사, 타사·없는 문서·비공개·버전 변경의 오류 변환, 파일 크기 표시, 인용 네 개 제한. |
| R3 | `version_service.require_manager`·`check_publication_access`·`read_publishable_candidate`, `documents_repo._check_candidate_creation` | 현재 사용자 관리자 역할·소속 회사 검사가 여러 곳에 반복된다. 회사 활성 상태 조회도 일부 따로 구현한다. | 이미 재검증된 current와 대상 회사 ID를 받는 작은 순수 권한 정책을 공유한다. SQL 조회·행 잠금까지 한 함수로 뭉치지 않는다. 회사 활성 조회는 기존 company_is_active를 더 낮은 적절한 모듈로 옮길 수 있는지 별도 확인한다. | 최초 검사와 외부 작업 후 재검사 모두 유지. 세션→사용자→회사→문서→버전의 기존 잠금 순서, 조회/쓰기 잠금 차이, 회사 계정 프로시저 분기 유지. |
| R4 | `company_admin.create_company_db_user`·`provision_company`·`main` | 관리자 연결과 앱 연결의 서버·포트·공통 DB 일치 검증이 세 경로에 반복된다. | 같은 연결 대상 검증을 company_admin의 작은 내부 함수 한 곳으로 모은다. DDL이나 계정 생성 전체를 범용 팩토리로 바꾸지 않는다. | 다른 서버·다른 DB·다른 dialect 거부, 같은 대상의 별도 관리자 계정 허용. 포트 None과 3306을 임의로 같게 취급하는 동작 변경은 이번 추출과 분리한다. |
| R5 | `chunks_repo.save_manifest`·`version_service.manifest_snapshot` | 같은 연결에서 같은 세 필드(id, chunk_index, content_hash)를 같은 순서로 읽어 dict 목록을 만드는 SQL이 반복된다. | `chunks_repo`에 호출자가 전달한 연결을 쓰는 원장 조회 함수를 둔다. 저장·완전성 스냅샷이 이 함수를 공유한다. | 자체 TX/commit을 만들지 않음, 버전 잠금 책임은 기존 호출자 유지, chunk_index 정렬과 원장 seal 계산 입력 동일성 보장. |

R1은 실패 처리 정책 전체를 바꾸는 제안이 아니다. 게시 성공이 확정된 뒤에는 실패 원장·벡터 정리를 재실행하지 않는 현재 보장을 유지해야 한다. R2도 DB 조회 한 번으로 외부 본문 작업 후 재검증까지 대신하는 제안이 아니다.

## 후순위 후보 3건

| 번호 | 대상 | 권장 범위 | 제한 |
|---|---|---|---|
| R6 | `schema.create_v4_schema`·`split_schema._statements` | 고정 schema_v4.sql의 주석 제거·문장 분리를 공통 읽기 함수로 모음 | 단일/분리 DDL 실행·스키마 검증·advisory lock은 별도로 유지. 범용 SQL 파서는 만들지 않음. |
| R7 | `runtime.transaction`·`company_routines._raise_routine_error`·`connection.main`·`company_admin.create_company_db_user` | SQLAlchemy orig.args에서 MySQL 숫자 오류 코드를 안전하게 읽는 작은 함수를 공유 | 숫자 추출만 공유. SIGNAL의 AccessError, 1062의 CONFLICT, CLI 진단 등 문맥별 매핑은 합치지 않음. |
| R8 | `source_files.file_digest`·`copy_original`, `documents_repo.validate_metadata` | 반복되는 파일 크기 상한 100MiB를 같은 상수로 표현 | 복사와 단독 해시 읽기 전체를 합치는 것은 우선순위 낮음. 한 번의 스트리밍에서 복사·해시 검증, 기존 파일 미덮어쓰기, fsync·hard link를 유지. |

## 공통화하지 않을 부분

| 대상 | 이유 |
|---|---|
| `jobs_repo._report_dict`와 `version_service.as_mapping` | 전자는 dataclass를 재귀 변환하고 후자는 청크 객체를 보존하는 얕은 변환이다. 이름만 비슷하다고 교체하면 임베딩 입력 객체 계약이 달라진다. |
| 상세 조회와 관리자 버전 이력의 접근 규칙 | 상세는 공개된 활성 운영 버전, 이력은 관리자에게 삭제·차단·과거 버전까지 허용한다. 같은 상태 필터를 적용하지 않는다. |
| `current_actor`와 회사 인증 프로시저 | 공통 계정의 Python 조회와 제한 회사 계정의 DEFINER 프로시저는 서로 다른 권한에서 같은 보안을 제공한다. 단순 중복 제거 대상으로 보지 않는다. |
| 단일 DB와 회사별 파일 잠금 | 잠금 키·회사 문맥·재진입·캐시 수명이 다르다. 비슷한 acquire/release 몇 줄을 합치기 위해 새 잠금 추상화를 만들지 않는다. |
| `actor`·회사 ID·버전 ID·`connection` 인자 | 권한·회사 범위·CAS·호출자 TX 계약이다. 전역 상태에 숨기거나 전부 임의의 큰 dict로 합치지 않는다. |

## 확인 위치

| 후보 | 핵심 근거 |
|---|---|
| R1 | `hyundap/db/upload_service.py:252`의 run_attempt 및 게시 후 최소 성공 결과 분기, `hyundap/ui/backend_documents.py:142`의 자동 반환 필수 필드 검증 |
| R2 | `hyundap/db/documents_repo.py:130`, `hyundap/ui/backend_documents.py:104`, `hyundap/db/download_service.py:14` |
| R3 | `hyundap/db/version_service.py:363`, `:421`, `:437`, `hyundap/db/documents_repo.py:265` |
| R4 | `hyundap/db/company_admin.py:37`, `:153`, `:237` |
| R5 | `hyundap/db/chunks_repo.py:9`, `hyundap/db/version_service.py:119` |
| R6 | `hyundap/db/schema.py:175`, `hyundap/db/split_schema.py:81` |
| R7 | `hyundap/db/runtime.py:84`, `hyundap/db/company_routines.py:151`, `hyundap/db/connection.py:82` |
| R8 | `hyundap/db/source_files.py:11`, `:49`, `hyundap/db/documents_repo.py:209` |

## 함수 전체 원장

아래는 함수별 인자·역할·후보 연결이다. 후보 표시가 없는 함수는 이번 순회에서 추가 패치 대상으로 선정하지 않았다. 이는 향후 개선 불필요 또는 정확성 검증 완료를 뜻하지 않는다.

### access_policy.py (1개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `grant_control_service_access` · 25 | `admin_engine, username, host='localhost'` | 이미 생성한 전용 계정에 공통 DB 권한표만 적용한다. 기존 권한 제거는 하지 않는다. | 미선정 |

### chunks_repo.py (3개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `save_manifest` · 9 | `version_id, chunks, *, connection=None` | 처리 중인 버전에 완전한 청크 목록을 한 번 저장한다. 같은 목록 재시도는 허용한다. | R5 |
| `_normalize_manifest` · 53 | `version_id, chunks` | 청크 목록의 필수 값·ID·해시·연속 순번을 검사하고 순번대로 정렬한 새 목록을 반환한다. | 미선정 |
| `_chunk_index` · 89 | `chunk` | 청크 dict에서 정렬 기준인 순번을 반환한다. sort가 항목마다 이 함수를 호출한다. | 미선정 |

### classification.py (4개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `classification_fields` · 13 | `report` | 저장·반환이 공유하는 네 분류 필드 계약. 미실행은 {}, 실패는 안전한 기본값이다. | 미선정 |
| `validate_auto_request` · 35 | `metadata` | 자동 구분값은 정확한 True만 허용하고 수동 유형과 동시에 받지 않는다. | 미선정 |
| `decision_fields` · 44 | `report, *, required=False` | 세 결정 필드를 함께 검사한다. 잘못된 결정값은 조용히 버리지 않는다. | 미선정 |
| `reusable_classification` · 61 | `reports` | 같은 후보의 최근 성공 수집에서 결정한 유형과 분류 결과를 함께 재사용한다. | 미선정 |

### clock.py (1개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `epoch_now` · 6 | `` | 현재 UTC Unix 초를 정수로 반환한다. 설정으로 단위를 바꾸지 않는다. | 미선정 |

### company_admin.py (4개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `admin_transaction` · 27 | `admin_engine` | 등록부 변경도 준비 관리자 연결에서 수행한다. 일반 서비스 권한을 늘리지 않는다. | 미선정 |
| `create_company_db_user` · 37 | `company_id, *, admin_engine, credential_path=None, _allow_creating=False` | 준비된 회사 DB에 전용 계정과 인증·라우팅 프로시저를 시험용으로 만든다. | R4/R7 |
| `provision_company` · 153 | `name, *, admin_engine, company_id=None` | 운영자 권한으로 회사 DB를 생성·검증한 후에만 ready로 등록한다. | R4 |
| `main` · 237 | `` | 새 빈 공통 DB 생성 또는 회사 등록을 명시적으로 실행한다. 비밀 URL은 출력하지 않는다. | R4 |

### company_credentials.py (9개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `credential_file_lock` · 25 | `path` | 교체되지 않는 별도 잠금 파일로 전체 읽기·수정·저장 구간을 보호한다. | 미선정 |
| `credential_source` · 43 | `` | 시험 파일과 호스팅 환경변수를 명시적으로 구분한다. | 미선정 |
| `credential_path` · 51 | `` | 상대 경로는 저장소 루트 기준으로 읽어 PC·작업 폴더에 의존하지 않는다. | 미선정 |
| `_validate` · 57 | `company_id, username, password` | 설정에서 읽거나 저장할 회사 ID와 계정·비밀번호를 검사한다. | 미선정 |
| `_load` · 67 | `path` | 설정 파일을 읽는다. 손상된 파일은 빈 설정으로 덮어쓰지 않는다. | 미선정 |
| `_load_config` · 80 | `path` | 호스팅 비밀이 없거나 손상되면 로컬 파일로 대체하지 않는다. | 미선정 |
| `read_credential` · 95 | `company_id, *, path=DEFAULT_PATH` | 설정에서 회사 항목을 찾아 (MySQL 계정명, 비밀번호) 튜플을 반환한다. | 미선정 |
| `has_credential` · 115 | `company_id, *, path=DEFAULT_PATH` | 새 계정 생성 전에 기존 회사 설정이 있는지 확인한다. | 미선정 |
| `save_credential` · 123 | `company_id, username, password, *, path=DEFAULT_PATH` | 새 회사 계정을 파일에 저장한다. 같은 값의 재요청은 아무것도 쓰지 않는다. | 미선정 |

### company_routines.py (7개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `procedure_statements` · 18 | `control_database, company_id, account_name` | 검증된 서버 식별자로 회사 전용 인증·라우팅 SQL을 만든다. | 미선정 |
| `_raise_routine_error` · 151 | `error` | DB 예외에서 오류 번호를 읽어 안전한 앱 예외를 발생시킨다. 반환값은 없다. | R7 |
| `authenticate_company_actor` · 176 | `database, actor, *, lock=False` | 회사 연결에서 세션·회사 권한을 검증하고 현재 사용자 정보를 반환한다. | 미선정 |
| `register_company_route` · 199 | `database, document_id, version_id, job_id` | 같은 트랜잭션의 회사 문서·버전·작업을 확인한 뒤 공통 라우팅을 등록한다. | 미선정 |
| `lookup_company_route` · 213 | `database, document_id=None, version_id=None, job_id=None` | 회사 계정에서 자기 회사의 문서·버전·작업 라우팅을 조회한다. | 미선정 |
| `lock_company_status` · 225 | `database` | 회사 행을 잠그고 현재 활성 상태를 반환한다. | 미선정 |
| `read_or_lock` · 41 | `query` | 조회 SQL 하나를 받아 잠금 유무를 선택하는 SQL 문자열로 반환한다. | 미선정 |

### connection.py (2개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `create_db_engine` · 14 | `database_url=None` | 로컬 접속 설정을 검사하고 지연 연결 방식의 SQLAlchemy Engine을 만든다. | 미선정 |
| `main` · 82 | `` | 현재 DB의 연결과 지원 스키마 구조를 읽기 전용으로 확인한다. | R7 |

### documents_repo.py (9개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `find_duplicate_document` · 27 | `company_id, sha256, *, exclude_version_id=None, connection=None` | 같은 회사·해시의 삭제/실패 포함 후보를 dict로 반환한다. 없으면 None이다. | 미선정 |
| `get_documents_with_active_version` · 62 | `document_ids, *, connection=None` | 문서 ID 목록으로 검색에 필요한 문서와 활성 버전 정보를 조회한다. | 미선정 |
| `get_document_detail` · 130 | `actor, document_id, *, version_id=None, connection=None` | 현재 사용자가 읽을 수 있는 활성 운영 문서의 상세 정보 dict를 반환한다. | R2 |
| `_check_detail_access` · 187 | `document, allowed_company_ids, version_id` | 상세 조회 행의 회사·공개 상태·요청 버전을 검사한다. 성공하면 반환값은 없다. | 미선정 |
| `validate_metadata` · 209 | `metadata` | 업로드 계약의 필수 값·길이·분류·해시를 검사해 새 dict로 돌려준다. | R8 |
| `create_document_version` · 249 | `actor, metadata, *, connection=None` | 관리자 권한 확인 후 문서·후보 버전·작업을 같은 짧은 TX에서 생성한다. | 미선정 |
| `_check_candidate_creation` · 265 | `database, actor, data` | 권한·기존 구조·중복을 검사하고 DB에서 재검증한 현재 사용자 dict를 반환한다. | R3 |
| `_prepare_document` · 299 | `database, data` | 검증된 업로드 dict로 기존 문서를 확인하거나 새 문서를 만들고 문서 ID를 반환한다. | 미선정 |
| `_insert_version_and_job` · 328 | `database, document_id, data` | 문서 ID와 검증된 업로드 dict로 버전·작업을 저장하고 세 가지 ID를 dict로 반환한다. | 미선정 |

### download_service.py (2개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `read_download_source` · 14 | `actor, document_id, version_id=None` | 상세 조회 권한을 재사용하고 같은 회사 연결에서 원본 저장 정보를 읽는다. | R2: 기존 재사용 사례 |
| `prepare_original_download` · 30 | `actor, document_id, version_id=None, *, upload_dir=None` | 검증된 사본과 소유 TemporaryDirectory를 반환한다. 호출자가 반드시 cleanup한다. | 미선정 |

### errors.py (1개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `__init__` · 24 | `self, code` | 정의된 code를 보관하고 사용자 데이터가 없는 안내를 예외 문자열로 설정한다. | 미선정 |

### ingest_adapter.py (1개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `call_ingest` · 13 | `function, source, metadata, *, find_by_hash, file_hash=None, classify=False, automatic=False, classification=None` | 선택 인자 지원 여부는 이곳에서만 판별하고 동일 후보의 재사용 결정값을 보존한다. | 미선정 |

### jobs_repo.py (15개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `normalize_report` · 23 | `report` | dict/dataclass 보고서에서 저장해도 되는 필드만 새 dict로 반환한다. | 미선정 |
| `_report_dict` · 51 | `report` | dict 또는 dataclass 입력을 dict로 반환하고 나머지 자료형은 거부한다. | 미선정 |
| `_report_identity` · 61 | `report` | 보고서 dict에서 단계·상태·문서 식별자를 검사해 새 dict로 반환한다. | 미선정 |
| `_report_metrics` · 81 | `report` | 보고서 dict에서 개수·모델명·소요 시간을 검사해 새 dict로 반환한다. | 미선정 |
| `_ingest_metrics` · 108 | `report, stage` | 수집 전용 필드의 타입·범위를 검사한다. 경고 원문은 읽거나 저장하지 않는다. | 미선정 |
| `_report_failures` · 152 | `report` | 보고서 dict에서 안전한 오류 코드와 실패 목록만 새 dict로 반환한다. | 미선정 |
| `_failure_detail` · 173 | `failure` | 실패 항목 하나에서 코드·청크 ID·재시도 여부만 새 dict로 반환한다. | 미선정 |
| `record_stage_result` · 198 | `job_id, report, *, connection=None` | 작업의 단계별 결과를 같은 TX에서 기록한다. 이전 단계의 지연 기록은 거부한다. | 미선정 |
| `_read_stage_target` · 217 | `database, job_id` | 작업 ID로 저장 대상을 조회하고 (작업 행, 문서·버전·회사 ID dict)를 반환한다. | 미선정 |
| `_check_ingest_count` · 240 | `database, version_id, count` | 잠근 버전의 예상·실제 청크 수와 보고서를 비교한다. 데이터는 변경하지 않는다. | 미선정 |
| `_check_stage_result` · 257 | `job, identity, clean` | 정규화한 보고서가 이 작업의 대상과 단계에 맞는지 검사한다. 반환값은 없다. | 미선정 |
| `_merge_stage_reports` · 277 | `stored_report, clean` | 저장된 JSON 또는 dict에 새 단계 결과를 반영해 보고서 dict를 반환한다. | 미선정 |
| `_store_stage_result` · 304 | `database, job_id, clean, reports` | 검사가 끝난 보고서 dict와 현재 단계·안전한 오류 문구를 DB에 저장한다. 반환값은 없다. | 미선정 |
| `store_job_result` · 324 | `database, job_id, stage, report, *, status=None, error_code=None, error_message=None` | 검증된 단계·보고서·종료 상태를 한 UPDATE로 저장한다. 호출자 TX에 참여한다. | 미선정 |
| `reset_job_for_retry` · 344 | `database, job_id, report` | 검증된 작업을 같은 TX에서 재시도 상태로 저장한다. 반환값·직접 커밋은 없다. | 미선정 |

### lifecycle.py (2개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `close_service_engines` · 7 | `` | 요청이 끝난 서버 종료 시 호출한다. 회사 풀을 먼저, 공통 풀을 나중에 정리한다. | 미선정 |
| `service_lifespan` · 17 | `app` | FastAPI/Gradio 서버의 정상 종료에 연결한다. 강제 kill은 OS 정리에 의존한다. | 미선정 |

### processing.py (2개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `workflow_file_lock` · 16 | `root` | 같은 스레드의 게시·복구 중첩 호출도 재진입할 수 있도록 잠금 객체를 공유한다. | 미선정 |
| `processing_lock` · 22 | `` | 최대 30초 대기한다. 프로세스 종료 시 OS가 잠금을 회수하며 파일은 삭제하지 않는다. | 미선정 |

### runtime.py (8개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `split_layout` · 13 | `` | 분리 모드는 명시적으로 켠다. 기존 v4 DB를 자동 변환하지 않는다. | 미선정 |
| `company_accounts_enabled` · 23 | `` | 회사별 계정 모드 설정을 검증한다. 잘못된 설정은 공유 계정으로 대체하지 않는다. | 미선정 |
| `isolated_company_connection` · 33 | `connection` | 명시적으로 활성화한 회사 계정 연결인지 확인한다. | 미선정 |
| `shared_table` · 40 | `name, *, connection=None` | 서버가 정한 공통 테이블만 식별자로 만든다. 사용자 입력 SQL은 받지 않는다. | 미선정 |
| `get_engine` · 53 | `` | 프로세스에서 연결 풀 하나를 재사용한다. 환경 변경 후에는 캐시를 비워야 한다. | 미선정 |
| `transaction` · 84 | `connection=None, *, company_id=None, document_id=None, version_id=None, job_id=None, control=False` | 연결이 없으면 짧은 트랜잭션을 열고, 있으면 호출자의 트랜잭션에 참여한다. | R7 |
| `require_id` · 134 | `value` | 기존 문자열 ID를 보존하며 비어 있거나 36자를 넘는 ID를 거부한다. | 미선정 |
| `require_hash` · 146 | `value` | 소문자 SHA-256 hex 문자열을 확인하고 그대로 반환한다. | 미선정 |

### schema.py (9개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `_read_layout` · 17 | `connection, *, exclude_company_view=False` | 버전 기록 전에도 컬럼·인덱스·FK·CHECK를 읽는다. 업무 행은 읽지 않는다. | 미선정 |
| `snapshot` · 67 | `connection` | 관리 연결의 전체 구조와 단일 버전 행을 반환한다. 조회 오류는 전달한다. | 미선정 |
| `_ascii_literal` · 77 | `match` | re.sub가 찾은 문자셋 표기에서 ASCII 문자열의 접두사만 제거한다. | 미선정 |
| `_expression` · 89 | `value` | MySQL 메타데이터 표현식의 공백·식별자 인용 표기만 정규화한다. | 미선정 |
| `_structure` · 106 | `layout` | 컬럼 속성과 모든 제약을 순서 독립 dict로 만들어 구조를 비교한다. | 미선정 |
| `validate_v4` · 151 | `layout` | 확정 계약과 모든 테이블·컬럼 속성·제약을 비교해 구조 오류 목록을 반환한다. | 미선정 |
| `create_v4_schema` · 175 | `engine` | 명시적으로 받은 Engine의 빈 MySQL DB에 최종 v4를 직접 생성한다. | R6 |
| `audit_counts` · 229 | `connection` | 읽기 전용 정합성 위반 건수를 반환한다. 외부 벡터 검사는 포함하지 않는다. | 미선정 |
| `main` · 243 | `` | 명시적 --create 또는 읽기 전용 --inspect/--check를 실행한다. 비밀값은 출력하지 않는다. | 미선정 |

### sessions_repo.py (6개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `token_hash` · 23 | `token` | 32바이트 이상 토큰의 문자열 형태를 검사하고 DB 비교용 SHA-256을 반환한다. | 미선정 |
| `current_actor` · 31 | `actor, *, connection, lock=False` | 서버 actor dict와 DB 연결을 받아 현재 권한을 담은 새 actor dict를 반환한다. | 미선정 |
| `_session_credentials` · 63 | `actor` | 서버 actor dict의 입력을 검사하고 (세션 ID, 비교용 토큰 해시)를 반환한다. | 미선정 |
| `_read_valid_session` · 75 | `connection, session_id, digest, lock` | ID·토큰 해시가 일치하고 만료·폐기되지 않은 세션 DB 행을 반환한다. | 미선정 |
| `_read_active_user` · 95 | `connection, session, lock` | 세션 DB 행에 연결된 활성 사용자를 반환하고 권한 버전 변경을 거부한다. | 미선정 |
| `_check_company_role` · 111 | `connection, user, lock` | 사용자 DB 행의 역할·소속 회사 유효성을 검사하고 표준 역할명을 반환한다. | 미선정 |

### source_files.py (4개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `file_digest` · 11 | `path` | 실제 파일을 1MiB씩 읽어 SHA-256과 크기를 구한다. 비어 있거나 상한을 넘으면 거부한다. | R8 |
| `source_destination` · 32 | `root, company_id, document_id, version_id, suffix` | 안전한 식별자로 순수 경로를 계산한다. 실제 링크·루트 검사는 TX 밖에서 수행한다. | 미선정 |
| `copy_original` · 49 | `source, destination, expected_hash, expected_size` | 기존 파일을 덮지 않고 복사하면서 다시 해시를 검사한다. 복사 중 원본 변경도 검출한다. | R8 |
| `resolve_stored_source` · 80 | `source, root` | DB의 원본 경로와 저장 루트가 절대 경로인지 검사하고 루트 내부 Path를 반환한다. | 미선정 |

### split_schema.py (6개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `_extra_contract` · 48 | `name` | 신규 등록부와 소속 표의 고정 계약이다. 실측 결과로 기대값을 만들지 않는다. | 미선정 |
| `_statements` · 81 | `kind, control_name=None` | 고정 v4 SQL에서 해당 역할의 테이블만 선택한다. 관리 DB 참조만 명시적으로 바꾼다. | R6 |
| `_base_errors` · 117 | `connection, role, company_id=None, control_name=None, *, layout=None` | 기본 계약을 비교하고 (오류, 실측 layout, 비교용 구조)를 반환한다. | 미선정 |
| `validate_control` · 170 | `connection, *, layout=None` | 공통 DB의 인증/등록 구조를 검사한다. 같은 점검의 실측 layout만 재사용한다. | 미선정 |
| `validate_company` · 185 | `connection, control_name, company_id` | 회사 DB의 인증 테이블 부재·소속·공통 FK·원장 제약을 검사한다. | 미선정 |
| `create_split_schema` · 197 | `engine, *, control_name=None, company_id=None` | 명시한 빈 DB만 생성한다. 실패 후 자동 DROP/재실행은 하지 않는다. | 미선정 |

### tenancy.py (9개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `quoted_database` · 32 | `name` | DB 이름은 서버 관리 식별자만 허용하고 SQL 식별자로 인용한다. | 미선정 |
| `company_for_reference` · 39 | `*, document_id=None, version_id=None, job_id=None, connection=None` | 내용 없이 저장한 전역 식별자 목록에서 대상 회사를 찾는다. | 미선정 |
| `get_company_engine` · 64 | `company_id` | 활성 등록만 연결한다. 최대 32개 회사 풀을 유지하고 초과 시 명시적으로 거부한다. | 미선정 |
| `routed_engine` · 121 | `*, company_id=None, document_id=None, version_id=None, job_id=None, connection=None` | 회사 또는 업무 ID로 회사 Engine을 선택한다. 대상을 받지 않으면 None이다. | 미선정 |
| `company_scope` · 148 | `company_id` | with 본문이 처리할 회사를 고정하고 종료 시 이전 회사 문맥으로 돌아간다. | 미선정 |
| `processing_lock` · 172 | `fallback, *, publication=False` | 분리 모드는 회사별 OS 잠금, 단일 DB 모드는 전역 OS 잠금과 Python 잠금을 쓴다. | 미선정 |
| `invalidate_company_engine` · 220 | `company_id` | 해당 회사의 캐시만 제거한다. 진행 중 연결·작업 잠금은 강제로 끊지 않는다. | 미선정 |
| `close_company_engines` · 237 | `` | 앱 종료/시험 정리 시 회사 연결 풀을 닫는다. 처리 중에는 호출하지 않는다. | 미선정 |
| `authorize_company` · 247 | `actor, company_id=None, *, manager=False` | 회사 DB를 열기 전에 공통 DB의 현재 세션과 역할·허용 회사를 확인한다. | 미선정 |

### upload_service.py (22개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `get_ingest_function` · 30 | `function=None` | 주입한 함수 또는 팀 수집 진입점을 선택한다. 미구현 뼈대는 안전하게 거부한다. | 미선정 |
| `failure_code` · 44 | `error` | 예외에서 안전한 코드만 가져온다. 원시 메시지·URL·접속 비밀값은 저장하지 않는다. | 미선정 |
| `upload_result` · 56 | `ids, *, connection=None, **status` | 이번 시도의 수집 성공 정보만 반환한다. 실패 시 유형은 공개값이 아닌 후보값이다. | R1 |
| `record_upload_failure` · 72 | `ids, stage, error, backend` | 게시 미완료는 복구에 맡기고, 비활성 처리 후보만 실패 기록·벡터 정리한다. | 미선정 |
| `read_attempt_source` · 107 | `ids` | 문서·버전 ID로 저장 원본을 확인하고 (파일 경로, 수집 정보, 검증한 해시)를 반환한다. | 미선정 |
| `save_ingest_chunks` · 146 | `ids, company_id, result, *, automatic=False` | 성공한 수집 결과의 청크를 검사·기록하고 원래 청크 목록을 반환한다. | 미선정 |
| `verify_uploaded_chunks` · 198 | `ids, backend` | DB 청크 목록과 벡터 저장 결과를 대조하고 성공 보고서를 남긴다. 반환값은 없다. | 미선정 |
| `ingest_and_save_chunks` · 210 | `source, info, ids, ingest_function, file_hash=None` | 원본 Path와 수집 정보로 수집 단계를 실행하고 (청크 목록, 오류 코드)를 반환한다. | 미선정 |
| `embed_and_record_result` · 236 | `chunks, job_id, backend` | 청크 목록을 임베딩하고 단계 보고서를 저장한다. 성공은 None, 실패는 오류 코드다. | 미선정 |
| `run_attempt` · 252 | `actor, ids, expected_active_id, ingest_function, backend` | 원본 확인 → 수집 → 임베딩 → 검증 → 게시 순서로 실행하고 결과 dict를 반환한다. | R1 |
| `prepare_upload_source` · 298 | `source_path, metadata, upload_dir` | 서버 파일을 읽어 (원본 Path, 저장 루트 Path, 저장 정보 dict)를 준비한다. | 미선정 |
| `create_upload_candidate` · 327 | `actor, company_id, values, source, root` | 후보와 최종 저장 경로를 한 번에 저장하고 (ID dict, 이전 활성 ID, 목적지 Path)를 반환한다. | 미선정 |
| `upload_document` · 358 | `actor, source_path, metadata, *, ingest_document=None, embedding=None, upload_dir=None` | 회사별 DB 문맥에서 기존 원본→게시 처리를 실행한다. 인증은 공통 DB에서 재검증한다. | 미선정 |
| `_upload_document` · 372 | `actor, source_path, metadata, *, company_id, ingest_document=None, embedding=None, upload_dir=None` | 관리자 업로드의 원본 저장부터 게시까지 연결하고 결과 ID·상태를 반환한다. | 미선정 |
| `check_retry_allowed` · 400 | `document, version, reports` | 문서·버전·보고서 dict가 재시도 가능한 상태인지 확인한다. 반환값은 없다. | 미선정 |
| `prepare_retry` · 416 | `actor, version_id` | 권한·후보를 잠가 재시도를 준비하고 ID·이전 활성 ID·원본 정보를 dict로 반환한다. | 미선정 |
| `read_retry_records` · 448 | `database, actor, version_id` | 관리자 권한으로 재시도 대상의 (문서, 버전, 작업, 보고서) dict들을 읽는다. | 미선정 |
| `build_retry_report` · 468 | `root, reports` | 저장 루트 Path와 기존 보고서 dict로 이전 시도를 보존한 새 보고서 dict를 만든다. | 미선정 |
| `reset_retry_records` · 488 | `database, version_id, job_id, report` | 같은 DB 연결에서 버전·작업을 재시도 시작 상태로 저장한다. 반환값은 없다. | 미선정 |
| `retry_upload` · 502 | `actor, version_id, *, ingest_document=None, embedding=None, source_path=None` | 전역 식별자로 회사 DB를 선택한 뒤 기존 후보를 재시도한다. | 미선정 |
| `_retry_upload` · 515 | `actor, version_id, *, ingest_document=None, embedding=None, source_path=None` | 재시도 준비 → 경로 확인 → 이전 벡터 정리 → 처리 순서로 실행하고 결과 dict를 반환한다. | 미선정 |
| `find_by_hash` · 217 | `company_id, sha256` | 서버의 현재 후보 문맥에만 묶인 조회다. 임의 회사·해시 제외를 허용하지 않는다. | 미선정 |

### version_service.py (31개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `get_embedding_backend` · 28 | `embedding=None` | 벡터 처리 함수 5개를 이름으로 찾을 수 있는 dict로 반환한다. | 미선정 |
| `as_mapping` · 66 | `value` | 외부 결과를 dict로 읽는다. dataclass는 바깥 필드만 옮기고 내부 객체는 보존한다. | 미선정 |
| `call_external` · 87 | `function, *args, **kwargs` | 팀 외부 함수의 예외 원문을 안전한 처리 실패로 변환한다. 비밀값은 전달하지 않는다. | 미선정 |
| `read_candidate` · 99 | `database, document_id, version_id, *, lock=False` | 문서와 버전을 일치시켜 읽는다. 잠금이 필요하면 문서→버전 순서를 따른다. | 미선정 |
| `manifest_snapshot` · 119 | `database, version` | 원장과 후보 메타데이터의 해시를 만든다. 외부 검사 이후 변경 여부를 다시 비교한다. | R5 |
| `validate_completeness` · 141 | `document_id, version_id, *, embedding=None` | DB 기준 ID·순번·본문 해시와 실제 벡터의 ID·본문·회사/문서/버전을 대조한다. | 미선정 |
| `chunk_matches_manifest` · 163 | `hit, expected, seen, identity` | 벡터 조회 결과 하나가 DB 원장의 식별자·본문과 같은지 bool로 반환한다. | 미선정 |
| `compare_stored_chunks` · 194 | `identity, version, manifest, seal, ids, hits` | DB 원장과 외부 청크 목록을 비교해 완전성 결과 dict를 만든다. | 미선정 |
| `collect_chunk_comparison` · 218 | `identity, manifest, ids, hits` | 원장·실제 청크를 대조해 개수 판정에 필요한 자료를 dict로 반환한다. | 미선정 |
| `inspect_chunk_contents` · 251 | `identity, expected, hits` | 실제 청크의 내용·소속·중복을 검사하고 (확인한 ID 집합, 잘못된 ID 목록)을 반환한다. | 미선정 |
| `read_job` · 277 | `database, version_id, *, lock=False` | 버전당 하나인 작업과 안전한 JSON 기록을 읽는다. 중복 작업은 자동 선택하지 않는다. | 미선정 |
| `write_publication` · 300 | `database, job, reports, publication, *, complete=False, error_code=None` | 복구에 필요한 이전 포인터·공개 상태·단계를 작업 JSON에 원자적으로 저장한다. | 미선정 |
| `ensure_no_pending_publication` · 322 | `database, document_id, *, except_job=None` | 같은 문서에 미복구 외부 전환이 있으면 새 게시를 막는다. 호출자는 문서 행을 잠근다. | 미선정 |
| `company_is_active` · 340 | `database, company_id, *, lock=False` | 대상 회사의 활성 값을 반환한다. 조회 대상이 없으면 None이다. | 미선정 |
| `require_manager` · 363 | `actor, database, company_id` | 현재 세션·관리자 역할·회사 권한을 확인한다. 입력 문맥의 역할은 신뢰하지 않는다. | R3 |
| `publish_version` · 380 | `actor, document_id, candidate_id, expected_active_id, *, embedding=None` | 회사 범위를 고정한 뒤 검증·CAS·벡터 공개·복구의 기존 순서를 실행한다. | 미선정 |
| `_publish_version` · 391 | `actor, document_id, candidate_id, expected_active_id, *, embedding=None` | 완전한 운영 후보만 조건부 게시하고 커밋 후 벡터를 활성화한다. | 미선정 |
| `check_publication_access` · 421 | `actor, document_id, candidate_id` | 외부 벡터를 읽기 전에 현재 관리자 권한을 확인하고 문서의 회사 ID를 반환한다. | R3 |
| `read_publishable_candidate` · 437 | `database, actor, document_id, candidate_id, company_id, seal` | 잠금 안에서 권한·상태·원장 변경을 검사하고 (문서 dict, 버전 dict)를 반환한다. | R3 |
| `prepare_publication` · 467 | `actor, document_id, candidate_id, company_id, expected_active_id, seal` | DB 활성 버전과 복구 기록을 함께 저장한다. 벡터 활성화가 필요하면 True다. | 미선정 |
| `check_publication_conflict` · 498 | `database, document, version, expected_active_id` | 잠근 문서·후보 버전이 다른 게시와 경쟁하지 않는지 검사한다. 반환값은 없다. | 미선정 |
| `store_pending_publication` · 516 | `*, database, document, candidate_id, expected_active_id, job, reports` | 검사된 후보를 DB 활성 버전으로 바꾸고 벡터 활성화 대기 기록을 함께 저장한다. | 미선정 |
| `activate_candidate` · 541 | `backend, document_id, candidate_id, expected_count` | DB 커밋 후 후보 벡터를 활성화한다. 실패하면 복구를 시도하고 안전한 오류를 낸다. | 미선정 |
| `finish_publication` · 556 | `document_id, candidate_id` | 벡터 활성화 후 DB 상태가 그대로인지 확인하고 게시 완료를 기록한다. 반환값은 없다. | 미선정 |
| `recover_publication` · 576 | `document_id, version_id, *, embedding=None` | 서버 내부 복구 요청을 해당 회사 DB로만 라우팅한다. | 미선정 |
| `_recover_publication` · 585 | `document_id, version_id, *, embedding=None` | 서버 운영자가 미완료 게시를 이전 상태로 복구한다. 사용자 입력으로 직접 노출하지 않는다. | 미선정 |
| `check_previous_version` · 606 | `database, document, document_id, previous_id` | 이전 버전을 다시 공개할 수 있는지와 예상 청크 수를 (bool, 개수)로 반환한다. | 미선정 |
| `prepare_recovery` · 627 | `document_id, version_id` | DB 포인터를 조건부로 되돌리고 외부 복구에 필요한 값들을 dict로 반환한다. | 미선정 |
| `restore_database_pointer` · 664 | `*, database, document, version_id, publication, eligible` | 현재 포인터가 실패 후보일 때만 되돌리고 복구 후 활성 버전 ID를 반환한다. | 미선정 |
| `restore_vectors` · 689 | `backend, document_id, version_id, recovery` | SQL 잠금 없이 이전 벡터 복원과 후보 삭제를 수행한다. 모두 끝나면 True다. | 미선정 |
| `finish_recovery` · 712 | `document_id, version_id` | 후보가 비활성인지 마지막으로 확인하고 실패 상태·복구 완료를 저장한다. 반환값은 없다. | 미선정 |

### versions_repo.py (2개)
| 함수·위치 | 인자 | 역할 | 추가 후보 |
|---|---|---|---|
| `list_document_versions` · 12 | `actor, document_id, *, connection=None` | 회사 권한이 있는 관리자의 버전 이력을 순번대로 반환한다. 내부 경로는 제외한다. | 미선정 |
| `mark_failed` · 67 | `version_id, *, connection=None` | 서버 내부에서 비활성 처리 후보만 failed로 바꾼다. 정상 활성 버전은 보존한다. | 미선정 |
