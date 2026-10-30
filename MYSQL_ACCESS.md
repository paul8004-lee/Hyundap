## 팀 공용 MySQL 연결

### develop을 처음 받은 팀원

현재 사용 중인 DB는 **공통 인증 DB + 회사별 업무 DB의 split 구조**입니다. `.env.example`은 이 구조의 배포용 설정 예제입니다. 실제 비밀번호·API 키·회사별 DB 계정은 Git에 포함하지 않으므로 운영자에게 한 번 별도로 받아야 합니다.

1. 저장소 루트에서 `python -X utf8 -m pip install -r requirements.txt`를 실행합니다.
2. `.env`가 없는 새 작업 폴더에서 `.env.example`을 `.env`로 복사합니다. 기존 `.env`는 덮어쓰지 않습니다. 예제에 들어 있는 **공개 DB 주소·공통 DB 이름·TLS 인증서 경로는 유지**하고 `DATABASE_URL`의 `YOUR_DB_USER`, `URL_ENCODED_PASSWORD`를 운영자가 발급한 값으로 바꿉니다. `OPENAI_API_KEY`도 입력합니다.
3. 운영자가 전달한 회사별 계정 JSON을 루트의 `.env.company-databases.json`에 둡니다. `HYUNDAP_COMPANY_CREDENTIALS_FILE`의 상대 경로는 저장소 루트를 기준으로 읽습니다. 다른 PC의 `C:\Users\...` 절대 경로를 복사하지 않습니다.
4. `python -m hyundap.db.connection`으로 `DB_CONNECTION_OK`를 확인합니다. 현재 split 설정은 `schema_version=4`, 공통 테이블 9개입니다. 이 명령은 공통 DB만 검사하며 회사 DB의 검색 권한은 로그인 후 별도로 확인합니다.
5. `python -m hyundap.app`을 실행하고 `http://127.0.0.1:7860`에 접속합니다. 문서 원본·벡터 저장소 공유 조건은 아래 안내도 확인합니다.

**현재 접속 대상 (2026-10-30 검증):** Railway `MySQL-RKHA`의 공개 TCP 주소는 `thomas.proxy.rlwy.net:27932`, 공통 DB는 `p3_local_control_c6e5fc8de908`입니다. 기존 SSH 터널과 서버 UUID·DB 이름이 같고 회사 DB 5곳의 제한 계정 접속도 확인했습니다. 과거 `127.0.0.1:33992` 대신 이 공개 주소를 사용하면 팀원 PC에 SSH 키나 별도 터널이 필요 없습니다. Railway 내부 전용 주소나 다른 DB의 과거 공개 주소를 대신 사용하지 않습니다.

공개 접속은 `.env.example`의 `DB_SSL_CA=hyundap/db/certs/railway-mysql-ca.crt`로 TLS를 사용합니다. 저장소에 포함된 파일은 해당 DB의 **공개 CA 인증서**이며 개인 키가 아닙니다. CA 서명을 검증하고 인증서 오류 시 평문으로 재시도하지 않습니다. MySQL 자체 발급 인증서는 Railway 프록시 도메인을 포함하지 않아 도메인 이름 검증은 적용하지 않습니다. 다른 서버를 명시한 별도 연결에는 이 팀 DB 전용 CA를 강제로 적용하지 않습니다.

URL 형식은 `mysql+pymysql://사용자:URL인코딩된비밀번호@공개호스트:포트/공통DB이름`입니다. 예제의 계정·비밀번호 자리표시는 반드시 바꿉니다. 앱은 임의 DB로 전환하거나 새 DB를 생성하지 않습니다. `DB_PROVISION_URL`은 DB·계정 생성 권한을 가진 운영자만 별도로 사용하며 팀원 앱 실행에 필요하지 않습니다.

| 점검 오류 | 조치 |
|---|---|
| `DB_UNREACHABLE` | 주소·포트·네트워크를 확인합니다. localhost는 해당 PC의 DB/터널이 필요합니다. |
| `DB_HOST_NOT_FOUND` | 오타 또는 Railway 내부 전용 주소인지 확인합니다. |
| `DB_AUTH_FAILED` / `DB_ACCESS_DENIED` | 발급 계정·비밀번호·대상 DB 권한을 확인합니다. |
| `DB_NOT_FOUND` | 공통 DB 이름을 확인합니다. 초기화해서 새 DB로 바꾸지 않습니다. |
| `incompatible schema` | DB 구조와 `HYUNDAP_DB_LAYOUT`이 일치하는지 확인합니다. |

### 2026-10-30 배포 전 검증

서로 다른 폴더에 코드·스키마 계약·공개 CA를 복사하고, 개인 환경변수를 제거한 별도 프로세스에서 공통 DB 및 회사 DB 5곳의 TLS 연결과 회사 ID 일치를 확인했습니다. 실제 비밀값은 Git 제외 설정으로만 전달했습니다. 관련 자동 검사 28개 통과, 외부 DB 조건부 검사 24개는 건너뛰었습니다. 공개 TCP 프록시는 `ACTIVE`입니다. DB 초기화·마이그레이션·업무 데이터 변경은 수행하지 않았습니다.

### 이전 단일 DB 접속 안내

아래 주소는 **과거 단일 `railway` DB**의 주소이며 현재 split 시험 DB 주소가 아닙니다. 해당 DB를 명시적으로 사용하는 경우에만 `HYUNDAP_DB_LAYOUT=single`과 함께 적용합니다.

GitHub 레포 접근 권한과 MySQL 접속 권한은 별개입니다. 이 레포는 공개되어 있으므로 실제 계정·비밀번호·접속 URL이 채워진 `.env`는 커밋하지 않습니다. 팀원별 계정은 관리자가 별도로 발급하며, 아래 코드는 계정을 만들거나 DB를 초기화하지 않습니다.

1. Python 3.11 이상에서 `python -m pip install -r requirements.txt`를 실행합니다.
2. 레포 맨 위 폴더에 `.env` 파일을 만들고 본인에게 발급된 계정으로 `DATABASE_URL`을 채웁니다(형식은 아래 참고). 기존 `.env`가 있다면 덮어쓰지 말고 해당 항목만 추가합니다.
3. 저장소 맨 위 폴더에서 `python -m hyundap.db.connection`을 실행합니다. 최종 v4 전체 구조를 검증해야 성공합니다. v3나 과거 혼합 v4는 실패합니다. 성공은 앱 전체 구현이나 실제 인증/UI 연결 완료를 뜻하지 않습니다.
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

최종 v4는 11개 테이블을 빈 DB에 직접 생성합니다. #168의 사용자 결정으로 v3 이행 도구·호환 설정을 폐기했습니다. 기존 Railway DB를 자동 변경하지 않으며 이번 작업에서도 운영 DDL/DML은 실행하지 않았습니다.

`python -m hyundap.db.schema --inspect`와 `--check`는 읽기 전용입니다. 초기화는 **새 빈 DB URL을 지정한 뒤** `python -m hyundap.db.schema --create`로만 실행합니다. 기존 객체가 있으면 거부하며 자동 DROP·변환·덮어쓰기를 하지 않습니다. 앱 import나 연결 점검에서 생성하지 않습니다.

계약은 [DB_contract.md](docs/db-design/DB_contract.md), 테이블 정의는 [DB_schema.md](docs/db-design/DB_schema.md), 초기화·부분 실패 처리는 [V4_INITIALIZATION.md](docs/db-design/V4_INITIALIZATION.md)를 따릅니다. 기존 v3 이행과 운영 전환 절차는 제공하지 않습니다.

팀원 계정의 기본 범위는 `railway` DB의 조회·추가·수정·삭제이며, `CREATE`, `ALTER`, `DROP`, 계정 관리 권한은 포함하지 않습니다. 직접 SQL로 접속하는 계정은 앱의 회사별 접근 제한을 거치지 않습니다. 팀에서 빠진 참가자의 DB 권한은 관리자가 별도로 회수해야 합니다.

문서검색 앱에서 원본 파일·벡터를 PC별로 저장하는 경우 DB만 공유해서는 다른 PC의 문서를 처리할 수 없습니다. 공동 문서 작업은 원본·벡터 저장소가 연결된 공용 앱 서버에서 진행합니다.

연결 실패 시 계정·비밀번호, URL 인코딩, 호스트·포트, 회사/학교 네트워크의 외부 TCP 허용 여부를 확인합니다. 비밀번호가 포함된 오류 원문이나 `.env`를 이슈·PR·로그에 붙이지 않습니다.
