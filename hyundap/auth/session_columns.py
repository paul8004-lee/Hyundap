"""최종 v4 세션의 고정 INSERT와 필수 시간·CSRF 해시를 구성한다."""

import hashlib
import secrets

from hyundap.db.clock import epoch_now


def build_session_insert(base_params, ttl_minutes):
    """검증된 기본 인자와 TTL 분으로 (고정 SQL, 새 인자 dict)를 반환한다.

    시간은 UTC Unix 초로 고정하며 생성·활동 시각과 유휴/절대 만료를 함께 채운다.
    인증 expires_at도 같은 절대 만료로 만든다. 별도 CSRF 원문은 반환·저장하지 않는다.
    입력을 변경하거나 DB 구조를 조회하지 않으며 트랜잭션은 호출자가 소유한다.
    """
    now = epoch_now()
    expires = now + ttl_minutes * 60
    params = dict(base_params, created_at=now, last_seen_at=now,
                  idle_expires_at=expires, absolute_expires_at=expires,
                  csrf_token_hash=hashlib.sha256(secrets.token_urlsafe(32).encode('utf-8')).hexdigest())
    sql = '''
        INSERT INTO sessions (id,user_id,token_hash,auth_version,csrf_token_hash,created_at,
                              last_seen_at,idle_expires_at,absolute_expires_at,expires_at)
        VALUES (:id,:user_id,:token_hash,:auth_version,:csrf_token_hash,:created_at,
                :last_seen_at,:idle_expires_at,:absolute_expires_at,FROM_UNIXTIME(:absolute_expires_at))
    '''
    return sql, params
