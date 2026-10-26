## 팀 공용 MySQL 연결

GitHub 레포 접근 권한과 MySQL 접속 권한은 별개입니다. 이 레포는 공개되어 있으므로 실제 계정·비밀번호·접속 URL이 채워진 `.env`는 커밋하지 않습니다. 팀원별 계정은 관리자가 별도로 발급하며, 아래 코드는 계정을 만들거나 DB를 초기화하지 않습니다.

1. Python 3.11 이상에서 `python -m pip install -r requirements-db.txt`를 실행합니다.
2. 레포 맨 위 폴더에 `.env` 파일을 만들고 본인에게 발급된 계정으로 `DATABASE_URL`을 채웁니다(형식은 아래 참고). 기존 `.env`가 있다면 덮어쓰지 말고 해당 항목만 추가합니다.
3. 저장소 맨 위 폴더에서 `python -m hyundap.db.connection`을 실행합니다. `.env`는 같은 폴더에 둡니다. 기존 v3의 성공 예시는 `DB_CONNECTION_OK: schema_version=3, tables=9, version_storage_ready=False`입니다. v4는 필요한 구조 확인 후 `version_storage_ready=True`로 표시합니다. 이 값은 앱 전체 구현 완료나 파일·벡터 이행 완료를 뜻하지 않습니다.
4. 코드에서는 `hyundap.db.connection.create_db_engine()`을 사용합니다. 스키마 변경·초기화는 담당 관리자만 수행합니다.

| 항목 | 팀원 PC에서 사용하는 값 |
|---|---|
| 호스트 | `acela.proxy.rlwy.net` |
| 포트 | `24276` |
| 데이터베이스 | `railway` |
| 계정·비밀번호 | 관리자에게 개인별로 전달받은 값 |

`.env` 설정 형식은 다음과 같습니다. 비밀번호의 `@`, `#`, `/` 등은 URL 인코딩해야 하며, 환경 변수에 이미 설정된 `DATABASE_URL`이 `.env`보다 우선합니다.

```dotenv
DATABASE_URL=mysql+pymysql://본인계정:URL인코딩된비밀번호@acela.proxy.rlwy.net:24276/railway
```

```python
from sqlalchemy import text
from hyundap.db.connection import create_db_engine

engine = create_db_engine()
try:
    with engine.connect() as connection:
        companies = connection.execute(
            text('SELECT id, name FROM companies WHERE is_active = :active'),
            {'active': 1},
        ).mappings().all()
finally:
    engine.dispose()
```

기존 v3 문서 기준 DB는 업무 테이블 8개(`companies`, `users`, `documents`, `sessions`, `oauth_states`, `index_jobs`, `audit_logs`, `login_attempts`)와 버전 관리 테이블 `schema_migrations`를 사용합니다. #119에서는 `document_versions`, `document_chunks`를 추가하는 v4 준비 도구와 SQL 템플릿을 제공합니다. 이번 작업에서 공유 DB의 실제 구조는 미점검이며 DDL·DML도 미적용입니다.

스키마 조회는 `python -m hyundap.db.schema --inspect`, 검토용 SQL 생성은 `python -m hyundap.db.schema --prepare docs\db-design\migrations\004_local.sql`, v4 검증은 `python -m hyundap.db.schema --check`입니다. 세 명령 모두 SELECT·SHOW 등 읽기 전용 조회만 실행하며 SQL 생성은 로컬 파일에만 기록합니다. 기존 키 타입·문자셋·collation을 확인하고 불일치 시 중단합니다. 생성 SQL을 자동 실행하거나 버전 번호를 자동 변경하지 않습니다.

계약은 [DB_contract.md](docs/db-design/DB_contract.md), 테이블 정의는 [DB_schema.md](docs/db-design/DB_schema.md), 관리자 적용·실패 복구 절차는 [MIGRATION_v4.md](docs/db-design/MIGRATION_v4.md)를 따릅니다. 새 앱 구현과 레거시 자료의 명시적 이행 전에는 새 업로드를 활성화하지 않습니다.

팀원 계정의 기본 범위는 `railway` DB의 조회·추가·수정·삭제이며, `CREATE`, `ALTER`, `DROP`, 계정 관리 권한은 포함하지 않습니다. 직접 SQL로 접속하는 계정은 앱의 회사별 접근 제한을 거치지 않습니다. 팀에서 빠진 참가자의 DB 권한은 관리자가 별도로 회수해야 합니다.

문서검색 앱에서 원본 파일·벡터를 PC별로 저장하는 경우 DB만 공유해서는 다른 PC의 문서를 처리할 수 없습니다. 공동 문서 작업은 원본·벡터 저장소가 연결된 공용 앱 서버에서 진행합니다.

연결 실패 시 계정·비밀번호, URL 인코딩, 호스트·포트, 회사/학교 네트워크의 외부 TCP 허용 여부를 확인합니다. 비밀번호가 포함된 오류 원문이나 `.env`를 이슈·PR·로그에 붙이지 않습니다.
