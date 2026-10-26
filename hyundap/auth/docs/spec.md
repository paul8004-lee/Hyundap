# 인증 명세

책임: 전재경. [채택 계약](../../../docs/db-design/DB_contract.md)을 따른다. #118에서 계약을 결정했으며 런타임 구현은 #120이다.

| 기능 | 함수 계약 | 연계·책임 경계 |
|---|---|---|
| FUN-157 / 제약 L30 | login(identifier,password) → actor/session_token/expires_at | 박경환 UI는 전달·표시. auth가 이메일 조회·Argon2id·세션 발급 |
| F81 / FUN-253 | get_allowed_company_ids(actor) → set[str] | 이준영은 선택 회사와 교집합·범위 밖 선택 검증 |
| F126 / FUN-407 | verify_documents_access(actor,refs) → None 또는 예외 | 소용우 chatbot은 예외 변환·답변 전체 반환 제어 |

UserContext는 user_id,company_id,role,session_id,auth_version을 포함한다. role/company_id를 호출자가 조작해도 권한이 늘지 않도록 서버 세션·현재 계정을 재조회한다. 앱 역할은 소문자 3종이며 기존 대문자 역할을 경계에서 매핑한다.

세션은 8시간·자동 연장 없음, DB에는 토큰 SHA-256만 저장한다. 계정·회사 활성, 만료·폐기, users/sessions.auth_version 일치를 검사한다. 기존 해시 없는 계정에 비밀번호를 자동 생성하지 않는다. #119는 지원 컬럼만 준비하며 인증이 동작한다고 주장하지 않는다.

일반 Python 함수와 dict를 기본으로 구현하며 모델을 분리할 때만 Pydantic을 검토한다. 설정·의존성은 이성민과 연결하고 기존 WBS #72~#73의 담당 기록은 본 계약으로 구현 분담만 결정한다. 관리 역할 변경 기능은 P3 후속이다.
