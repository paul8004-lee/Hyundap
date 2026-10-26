## 팀 공용 MySQL 연결

GitHub 레포 접근 권한과 MySQL 접속 권한은 별개입니다. 이 레포는 공개되어 있으므로 실제 계정·비밀번호·접속 URL이 채워진 `.env`는 커밋하지 않습니다. 팀원별 계정은 관리자가 별도로 발급하며, 아래 코드는 계정을 만들거나 DB를 초기화하지 않습니다.

1. Python 3.11 이상에서 `python -m pip install -r requirements-db.txt`를 실행합니다.
2. `.env.example`을 `.env`로 복사하고 본인에게 발급된 계정으로 `DATABASE_URL`을 채웁니다. 기존 `.env`가 있다면 덮어쓰지 말고 해당 항목만 추가합니다.
3. `python -m db.connection`을 실행합니다. `DB_CONNECTION_OK: schema_version=3, tables=9`가 나오면 연결과 현재 스키마 버전 조회가 성공한 것입니다.
4. 코드에서는 `db.connection.create_db_engine()`을 사용합니다. 스키마 변경·초기화는 담당 관리자만 수행합니다.

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
from db.connection import create_db_engine

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

현재 DB는 업무 테이블 8개(`companies`, `users`, `documents`, `sessions`, `oauth_states`, `index_jobs`, `audit_logs`, `login_attempts`)와 버전 관리 테이블 `schema_migrations`를 사용합니다. 별도 검색·청크 테이블이 필요하면 관리자와 마이그레이션을 합의한 뒤 적용합니다.

팀원 계정의 기본 범위는 `railway` DB의 조회·추가·수정·삭제이며, `CREATE`, `ALTER`, `DROP`, 계정 관리 권한은 포함하지 않습니다. 직접 SQL로 접속하는 계정은 앱의 회사별 접근 제한을 거치지 않습니다. 팀에서 빠진 참가자의 DB 권한은 관리자가 별도로 회수해야 합니다.

문서검색 앱에서 원본 파일·벡터를 PC별로 저장하는 경우 DB만 공유해서는 다른 PC의 문서를 처리할 수 없습니다. 공동 문서 작업은 원본·벡터 저장소가 연결된 공용 앱 서버에서 진행합니다.

연결 실패 시 계정·비밀번호, URL 인코딩, 호스트·포트, 회사/학교 네트워크의 외부 TCP 허용 여부를 확인합니다. 비밀번호가 포함된 오류 원문이나 `.env`를 이슈·PR·로그에 붙이지 않습니다.
