"""B16/B24 · #190: 인증 권한 안의 회사 표시명만 읽는 서버측 UI 어댑터.

회사 목록은 실제 DB 값으로 구성하며 예시 회사명이나 클라이언트 역할로 추정하지 않는다.
회사를 등록하거나 권한을 바꾸지 않는다. DB/auth의 기존 검증 함수를 사용한다.
"""
from __future__ import annotations


def list_company_names(*, session: dict) -> list[dict[str, str]]:
    """서버 세션을 재검증하고 허용 회사의 ID·이름만 반환한다."""
    from sqlalchemy import bindparam, text

    from hyundap.auth.permissions import allowed_companies
    from hyundap.db.runtime import transaction
    from hyundap.db.sessions_repo import current_actor

    actor = {"session_id": session.get("session_id"), "_session_token": session.get("token")}
    requested = {code for code in session.get("allowed_company_ids") or [] if isinstance(code, str)}
    with transaction() as database:
        current = current_actor(actor, connection=database)
        allowed = allowed_companies(current, connection=database)
        company_ids = sorted(requested & allowed)
        if not company_ids:
            return []
        query = text("""
            SELECT id AS company_id, name AS company_name
            FROM companies WHERE is_active=1 AND id IN :company_ids
        """).bindparams(bindparam("company_ids", expanding=True))
        rows = database.execute(query, {"company_ids": company_ids}).mappings()
        return [{"company_id": row["company_id"], "company_name": row["company_name"]}
                for row in rows]
