# 인증 명세

책임: 전재경. [채택 계약](../../../docs/db-design/DB_contract.md)을 따른다. #120 런타임을 db02 작업 트리에서 구현했다. 공유 DB의 v4 적용·실측은 아직 완료하지 않았다.

| 기능 | 함수 계약 | 연계·책임 경계 |
|---|---|---|
| FUN-157 / 제약 L30 | login(identifier,password) → actor/session_token/expires_at | 박경환 UI는 전달·표시. auth가 이메일 조회·Argon2id·세션 발급 |
| F81 / FUN-253 | get_allowed_company_ids(actor) → set[str] | 이준영은 선택 회사와 교집합·범위 밖 선택 검증 |
| F126 / FUN-407 | verify_documents_access(actor,refs) → None 또는 예외 | 소용우 chatbot은 예외 변환·답변 전체 반환 제어 |

UserContext는 user_id,company_id,role,session_id,auth_version과 서버 내부 `_session_token`을 포함한다. 세션 ID만 복사하면 인증되지 않으며 토큰 해시·현재 계정·현재 회사로 재검증한다. 브라우저가 보낸 역할·회사는 권한 근거로 쓰지 않는다. `_session_token`을 UI·로그·JSON·LLM 입력에 넣지 않는다. 앱 역할은 소문자 3종이며 기존 대문자 역할을 매핑한다.

세션은 8시간·자동 연장 없음, DB에는 토큰 SHA-256만 저장한다. 계정·회사 활성, 만료·폐기, users/sessions.auth_version 일치를 검사한다. 해시 없는 계정·없는 이메일·잘못된 비밀번호는 동일한 INVALID_CREDENTIALS를 반환한다. 없는 계정에도 더미 Argon2id 검증을 수행한다. 비밀번호 공백을 제거하거나 비밀번호를 자동 생성하지 않는다. 새 해시는 관리자가 별도 계정 준비 절차로 설정해야 한다.

일반 Python 함수와 dict를 기본으로 구현하며 모델을 분리할 때만 Pydantic을 검토한다. 설정·의존성은 이성민과 연결하고 기존 WBS #72~#73의 담당 기록은 본 계약으로 구현 분담만 결정한다. 관리 역할 변경 기능은 P3 후속이다.

## 실제 호출 경로

```python
from hyundap.auth.login import login, authenticate, logout
from hyundap.auth.permissions import get_allowed_company_ids, verify_documents_access

result = login(email, password)  # 서버에서 실행; 비밀번호·결과 전체를 로그로 남기지 않는다.
token = result['session_token']  # 서버 세션 저장소에 보관한다.
actor = authenticate(token)  # 다음 요청에서 현재 문맥을 새로 조회한다.
companies = get_allowed_company_ids(actor)  # set[str]
verify_documents_access(actor, [(document_id, version_id)])  # 성공 None, 실패 AccessError/DbError
logout(actor)  # 현재 세션 폐기
```

세션·사용자·회사는 각각 PK로 조회하여 기존 컬럼 간 collation이 다르더라도 동작한다. DB 장애는 DbError(DB_UNAVAILABLE)로 전달하며 빈 권한 집합으로 숨기지 않는다. chatbot이 STATE_CHANGED를 자기 오류로 변환하고 최종 답변 반환을 중단한다.

의존성은 requirements-db.txt의 argon2-cffi==25.1.0이다. [공식 PasswordHasher API](https://argon2-cffi.readthedocs.io/en/stable/api.html)를 사용한다. 로그인 제한·비밀번호 재설정·사용자 생성·UI 연결은 이 단계의 신규 기능으로 추가하지 않았다.
