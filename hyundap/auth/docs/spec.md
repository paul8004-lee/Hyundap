# DB 접근 권한 명세

책임: 전재경의 DB 작업 중 회사 격리·문서 접근 재검증 범위다. 로그인·비밀번호 해싱/검증·세션 발급/폐기는 외부 인증 담당 책임이며 이 작업에서 제거했다. UI 담당의 기존 로그인 화면은 변경하지 않는다. [채택 계약](../../../docs/db-design/DB_contract.md)을 따른다.

| 기능 | 함수 계약 | 연계·책임 경계 |
|---|---|---|
| F81 / FUN-253 | get_allowed_company_ids(actor) → set[str] | 이준영은 선택 회사와 교집합·범위 밖 선택 검증 |
| F126 / FUN-407 | verify_documents_access(actor,refs) → None 또는 예외 | 소용우 chatbot은 예외 변환·답변 전체 반환 제어 |

actor는 외부 인증 담당이 서버에서 제공하는 user_id, company_id, role, session_id, auth_version과 서버 내부 `_session_token`을 포함한다. DB의 `sessions_repo.current_actor`는 토큰 SHA-256과 DB token_hash, 계정·회사 활성, 만료·폐기, users/sessions.auth_version 일치를 읽어 검사한다. 세션 발급·연장·폐기를 수행하지 않는다. 실제 인증 담당과의 어댑터 연결은 미완료이며 인증 결과를 준비하지 않으면 보호된 DB 함수를 호출할 수 없다.

브라우저가 보낸 역할·회사는 권한 근거로 쓰지 않는다. `_session_token`을 UI·로그·JSON·LLM 입력에 넣지 않는다. 앱 역할은 소문자 3종이며 기존 대문자 역할을 매핑한다. 기존 사용자·세션 컬럼은 데이터 구조로 유지한다.

```python
from hyundap.auth.permissions import get_allowed_company_ids, verify_documents_access

# actor는 외부 인증 담당이 서버에서 전달한 문맥이다.
companies = get_allowed_company_ids(actor)
verify_documents_access(actor, [(document_id, version_id)])
```

세션·사용자·회사는 각각 PK로 조회한다. DB 장애는 DbError(DB_UNAVAILABLE)로 전달하며 빈 권한 집합으로 숨기지 않는다. chatbot이 STATE_CHANGED를 자기 오류로 변환하고 최종 답변 반환을 중단한다.

테스트는 합성 사용자·세션 행을 직접 준비해 DB 접근 검증을 확인한다. 로그인 성공이나 비밀번호 검증의 테스트가 아니다. 공유 DB v4 적용·실측과 외부 인증 연동은 아직 완료하지 않았다.
