# DB 서비스 개선 결과

기준 브랜치: `test-01`, 시작 커밋: `4a20e558522770a48c2d9cb27b7dd45a60b4ba1e`.
작업일: 2026-10-29. 커밋·푸시는 수행하지 않았으며 변경은 현재 작업 트리에 있다.
최종 전체 시험: **576개 통과, 실패·오류·건너뜀 0개, 123.480초, 종료 코드 0**.
`python -m hyundap.tests.run_isolated_mysql`의 최종 로그와 종료 코드 파일을 확인했다.
이 실행기는 건너뜀이 있으면 실패 종료하므로 종료 코드와 OK를 함께 확인했다.
5개 개선 항목의 로컬 구현·검증을 완료했다. Git 반영과 실제 Railway 권한 적용은 별도다.

## 1. 수정 내용과 이유

| 항목 | 기존 문제 | 최종 변경 | 주요 함수 |
|---|---|---|---|
| 회사 비밀 저장 | 두 프로세스의 성공한 저장 중 한 회사 설정이 유실 | 같은 호스트 파일 잠금으로 읽기·검사·교체 전체 보호. 동일 값 멱등·다른 값 충돌 계약 유지 | company_credentials.credential_file_lock / save_credential |
| 원문 다운로드 | UI 어댑터가 미연결 오류 반환 | 권한·활성 버전·원본 해시를 확인한 사본을 브라우저별 60초·1회 보호 경로로 전송 | download_service.read_download_source / prepare_original_download, ui.downloads.issue_download / take_download |
| 프로세스 경쟁 | 회사별 RLock은 같은 프로세스 안에서만 작동 | 분리 DB 모드에서 같은 호스트·원본 루트의 회사별 OS 잠금. 기존 SQL 상태·조건부 게시·권한 유지 | tenancy.processing_lock |
| 공통 최소 권한 | 관리자 연결로만 검증돼 일반 서비스 권한 미확정 | 명시적 서비스 권한표 추가, 등록부 쓰기는 준비 관리자 연결로 이동. 회사 문서와 라우팅 공동 TX 유지 | access_policy.grant_control_service_access, company_admin.admin_transaction |
| 연결 풀 수명 | 전체 정리만 있고 회사별 교체·서버 종료 연결 부족 | 회사별 무효화, 기존 작업 잠금 유지, 서버 정상 종료 시 회사·공통 풀 정리 | tenancy.invalidate_company_engine, lifecycle.close_service_engines / service_lifespan |

## 2. 모듈·함수 역할

신규 DB 코드는 일반 함수 구조를 유지했다. UI의 DownloadMiddleware만 ASGI 프로토콜을
연결하기 위한 클래스이며 업무 처리는 함수에 둔다. 기존 학습 문서의 명시적 조건·반복·
한국어 독스트링과 단계 주석을 따랐다.

| 모듈 | 메인 함수·진입점 | 보조 함수·역할 |
|---|---|---|
| db/company_credentials.py | save_credential | credential_file_lock: 프로세스 잠금, 기존 _load/_validate 재사용 |
| db/source_files.py | 기존 공통 함수 이동 | file_digest, resolve_stored_source, copy_original. upload_service에서 기존 이름으로 import해 호출 계약 유지 |
| db/download_service.py | prepare_original_download | read_download_source: 상세 권한과 버전 정보 조회. 전송자가 임시 사본 정리 소유 |
| ui/downloads.py | DownloadMiddleware.__call__ | issue_download/take_download: 브라우저 바인딩·만료·1회 소비, file_blocks: 스트리밍 |
| db/access_policy.py | grant_control_service_access | CONTROL_SERVICE_PRIVILEGES: 고정 권한표, 입력·식별자 검사 |
| db/company_admin.py | 기존 provision_company/create_company_db_user | admin_transaction: 등록부 변경도 준비 관리자 연결 사용 |
| db/tenancy.py | 기존 get_company_engine/processing_lock, 신규 invalidate_company_engine | 캐시·파일 잠금 범위 분리, 연결 개수만 로깅 |
| db/lifecycle.py | service_lifespan | close_service_engines: 이미 생성된 풀만 정리 |

다운로드 연결을 위해 ui/backend_bindings.py, services.py, controller.py, search_page.py와
app.py를 수정했다. 실제 검증을 차단하던 controller의 폐기된 footer_company 참조도 제거했다.
일반 Gradio 파일 캐시에 실제 원본을 공개하지 않고 demo의 예시 파일 동작은 유지한다.

## 3. 검증 근거

| 검증 | 결과·범위 |
|---|---|
| 자격 증명 | 독립 프로세스 4개 신규 저장, 같은 회사 다른 값 경쟁, 시간 초과, 프로세스 종료 후 재획득, 원본 보존 포함 8개 통과 |
| 회사 등록 연계 | 초기 격리 MySQL 28개 통과, 설정 저장 실패 시 회사 비활성·미완료 계정 잠금 확인 |
| 다운로드 DB | 업로드·다운로드 MySQL 시험 35개 통과. 해시 변조·경로 이탈·누락 파일·오래된 버전·복사 중 권한 변경 거부 |
| 다운로드 UI/HTTP | 33개 통과. 다른 브라우저·만료 티켓·폐기 세션 거부, 동일 브라우저 1회 전송·사본 정리 확인 |
| 실제 브라우저 | 기존 회사 로그인→검색→원문 링크→DOCX 다운로드→로그아웃 확인. 39,352바이트, 제공된 원본과 SHA-256 일치 |
| 프로세스 경쟁 | split suite 22개 및 종료·재시도 1개 통과. 동일 회사 중복 방지, 다른 회사 병렬 처리, exit23 이후 원본·기존 활성 보존, 후보 중복 없이 한 번 게시 |
| 최소 권한 | 실제 MySQL 서비스 계정의 로그인·등록·문서 업무·원문 조회·세션 폐기 성공. 금지 조회/DDL/DELETE/등록 UPDATE/회사 직접 조회 차단. 라우팅 호출 뒤 장애에서 문서·버전·라우팅 공동 롤백 |
| 풀·종료 | 선택적 무효화·다른 회사 보존·진행 연결과 잠금 보존·비밀 교체 재연결·누락 거부·32개 경계·종료 정리 검증 |
| 정적 확인 | compileall, git diff --check, pip check 실행. 패키지 충돌 없음 |

이 표의 개별 실행 수는 서로 겹치므로 합산해 고유 시험 개수로 표시하지 않는다.
SQL은 격리 MySQL 8.4.13, 회귀의 AI·벡터는 대역이다. 실제 브라우저 검색은 기존 서비스와
승인된 가상 자료를 사용했으며 유료 부하 시험은 수행하지 않았다.

## 4. 최초 실패와 수정

1. Windows 파일 잠금 해제 뒤 잠금 파일이 제거되는 동작을 테스트가 허용하지 않아 실패했다.
   OS별 잠금 파일 잔존을 허용하되 원본 보존·임시 파일 제거 단언을 유지했다.
2. 관리자 연결로 등록부 쓰기를 옮긴 뒤 기존 공통 계정 모드의 GRANT 대상이 관리자로 바뀌었다.
   CURRENT_USER는 실제 업무 연결에서 읽도록 수정하고 기존 모드·최소 권한·실패 처리 3개를 재검증했다.
3. 전체 시험 첫 실행은 576개 중 오류 2개였다. source_files 이동 후 테스트의 패치 위치가
   upload_service.os를 가리킨 문제를 수정했다. Gradio 수명 시험은 launch 전 객체의 테마가
   없어 HTML 렌더링이 실패했으므로 실제 /config 경로와 종료 훅 검증으로 바로잡았다.
   화면 검증은 별도 실제 Chrome 결과로 구분한다. 최초 실패 로그를 보존했다.

## 5. 보존과 운영 범위

- 실제 Railway 회사·문서·계정·스키마·권한은 변경하지 않았다. 기존 시험 계정의 로그인·로그아웃과 읽기만 수행했다.
- v4 테이블 구조, 회사 권한 검사, 활성 버전·원본 보존, 같은 연결의 라우팅 공동 롤백을 유지했다.
- 로컬 비밀 파일은 계속 시험용 평문 파일이다. OS 접근 제한과 Git 제외가 필요하며 호스팅 비밀 저장소를 구현한 것으로 표시하지 않는다.
- 회사 처리 잠금은 같은 호스트·동일 원본 루트 범위다. 여러 서버의 분산 임대·네트워크 파일시스템 보장은 포함하지 않는다.
- 다운로드 티켓은 메모리 기반이어서 다중 워커는 세션 고정이 필요하다. 다른 워커·재시작 후에는 안전하게 거부된다.

진행 중 연결은 풀 무효화로 강제 종료하지 않는다. 비밀 교체는 요청을 중지하고 모든 워커에
무효화/재시작 절차를 적용한다. 정상 종료 훅과 강제 kill은 다르며 후자는 OS/DB 정리에 의존한다.
실제 32회사 부하 용량을 인증한 것이 아니라 캐시 경계와 연결 회수 계약을 검증했다.

## 6. 결과 파일

- 진행표: DB_SERVICE_IMPROVEMENT_CHECKLIST.md
- 운영 계약: DB_SERVICE_ACCESS_AND_LIFECYCLE.md
- 실행 로그: 저장소 밖 `C:\Users\user\Desktop\Hyundap_audit_20261029\improvement-full-suite*.log`
- 브라우저 증거: 같은 폴더 `improvement-download.png`
- 실제 내려받은 파일: 사용자의 Downloads 폴더에 보존. 검증 앱은 로그아웃 확인 후 종료했다.
