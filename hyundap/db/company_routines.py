"""회사 DB에서 사용할 인증·라우팅 프로시저의 시험 계약.

프로시저 생성은 운영자 연결에서만 수행한다. 회사 계정은 자신의 DB에 있는
프로시저만 실행하며 공통 인증 테이블을 직접 조회하지 않는다.
"""

import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from hyundap.auth.errors import AccessError
from .errors import DbError, mysql_error_number
from .runtime import require_id
from .tenancy import quoted_database


def procedure_statements(control_database, company_id, account_name):
    """검증된 서버 식별자로 회사 전용 인증·라우팅 SQL을 만든다.

    SQL은 반드시 해당 회사 DB에서 생성한다. 호출자의 MySQL 계정을 확인하고
    회사 ID와 DB 이름은 등록 시 고정한다. 실제 실행과 권한 부여는 호출자가 맡는다.

    반환값은 SQL 문자열 list다. 순서대로 인증, 라우팅 등록, 라우팅 조회,
    회사 상태 잠금, 회사 정보 VIEW를 만든다. 이 Python 함수 자체는 DB에 접속하지 않는다.
    IN p_*는 CALL로 받는 입력, DECLARE v_*는 SQL 안의 지역 변수,
    SELECT ... INTO는 조회값을 변수에 담는 구문, SIGNAL은 오류를 발생시키는 구문이다.
    SQL SECURITY DEFINER는 생성자의 권한으로 정해진 작업만 실행하게 하므로
    호출 계정 검사와 고정 회사 조건을 함께 유지해야 한다.
    """
    control = quoted_database(control_database)
    if not isinstance(company_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', company_id):
        raise DbError('INVALID_INPUT')
    if not isinstance(account_name, str) or not re.fullmatch(r'[a-zA-Z0-9_]{1,32}', account_name):
        raise DbError('INVALID_INPUT')
    guard = f"""
      IF SUBSTRING_INDEX(USER(), '@', 1) <> '{account_name}' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='ACCESS_DENIED';
      END IF;
    """
    def read_or_lock(query):
        """조회 SQL 하나를 받아 잠금 유무를 선택하는 SQL 문자열로 반환한다.

        바깥 함수에서만 네 번 호출하는 보조 함수다. 여기서 query를 실행하지 않는다.
        p_lock은 나중에 프로시저를 CALL할 때 전달되며, 1일 때만 FOR UPDATE를 쓴다.
        """
        return f'IF p_lock=1 THEN {query} FOR UPDATE; ELSE {query}; END IF;'

    session_query = f'''SELECT user_id,auth_version INTO v_user,v_session_version
        FROM {control}.sessions
       WHERE id=p_session AND token_hash=p_digest AND revoked_at IS NULL
         AND expires_at>UTC_TIMESTAMP(6)
         AND idle_expires_at>GREATEST(p_now,UNIX_TIMESTAMP())
         AND absolute_expires_at>GREATEST(p_now,UNIX_TIMESTAMP())'''
    user_query = f'''SELECT company_id,role,auth_version,is_active
        INTO v_company,v_role,v_user_version,v_active FROM {control}.users WHERE id=v_user'''
    own_company_query = f'''SELECT is_active INTO v_company_active
        FROM {control}.companies WHERE id=v_company'''
    target_company_query = f'''SELECT is_active INTO v_company_active
        FROM {control}.companies WHERE id='{company_id}' '''
    # 1. 인증: 세션→사용자→소속 회사→대상 회사 순서로 검사한다. 잠금 순서도 같다.
    authenticate = f"""
    CREATE PROCEDURE company_authenticate(
        IN p_session VARCHAR(36), IN p_digest CHAR(64), IN p_now BIGINT, IN p_lock TINYINT
    ) SQL SECURITY DEFINER
    BEGIN
      DECLARE v_user VARCHAR(36) DEFAULT NULL;
      DECLARE v_session_version INT DEFAULT NULL;
      DECLARE v_user_version INT DEFAULT NULL;
      DECLARE v_company VARCHAR(36) DEFAULT NULL;
      DECLARE v_role VARCHAR(32) DEFAULT NULL;
      DECLARE v_active INT DEFAULT 0;
      DECLARE v_company_active INT DEFAULT 0;
      {guard}
      IF p_lock IS NULL OR p_lock NOT IN (0,1) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='INVALID_STATE';
      END IF;
      {read_or_lock(session_query)}
      IF v_user IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='SESSION_EXPIRED'; END IF;
      {read_or_lock(user_query)}
      IF v_user_version IS NULL OR v_active<>1 OR v_session_version<>v_user_version THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='SESSION_EXPIRED';
      END IF;
      IF v_role IS NULL OR v_role NOT IN ('platform_admin','company_admin','employee') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='ACCESS_DENIED';
      END IF;
      IF v_role<>'platform_admin' AND (v_company IS NULL OR v_company<>'{company_id}') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='ACCESS_DENIED';
      END IF;
      IF v_company IS NOT NULL THEN
        {read_or_lock(own_company_query)}
        IF v_company_active<>1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='ACCESS_DENIED'; END IF;
      END IF;
      SET v_company_active=0;
      {read_or_lock(target_company_query)}
      IF v_company_active<>1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='ACCESS_DENIED'; END IF;
      SELECT v_user AS user_id,v_company AS company_id,v_role AS role,
             p_session AS session_id,v_user_version AS auth_version;
    END
    """
    # 2. 라우팅 등록: 회사 DB의 문서·버전·작업 세 값이 실제 연결된 경우만 공통 DB에 등록한다.
    insert_route = f"""
    CREATE PROCEDURE company_register_route(
        IN p_document VARCHAR(36), IN p_version VARCHAR(36), IN p_job VARCHAR(36)
    ) SQL SECURITY DEFINER
    BEGIN
      DECLARE v_count INT DEFAULT 0;
      {guard}
      SELECT COUNT(*) INTO v_count FROM documents d
        JOIN document_versions v ON v.document_id=d.id AND v.company_id=d.company_id
        JOIN index_jobs j ON j.version_id=v.id AND j.document_id=d.id AND j.company_id=d.company_id
       WHERE d.id=p_document AND v.id=p_version AND j.id=p_job AND d.company_id='{company_id}';
      IF v_count<>1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='INVALID_STATE'; END IF;
      INSERT INTO {control}.document_routes (document_id,version_id,job_id,company_id)
        VALUES (p_document,p_version,p_job,'{company_id}');
    END
    """
    # 3. 라우팅 조회: 입력 ID가 생략되면 그 조건만 생략한다. 회사 조건은 항상 적용한다.
    lookup_route = f"""
    CREATE PROCEDURE company_lookup_route(
        IN p_document VARCHAR(36), IN p_version VARCHAR(36), IN p_job VARCHAR(36)
    ) SQL SECURITY DEFINER
    BEGIN
      {guard}
      IF p_document IS NULL AND p_version IS NULL AND p_job IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='INVALID_STATE';
      END IF;
      SELECT DISTINCT company_id FROM {control}.document_routes
       WHERE company_id='{company_id}'
         AND (p_document IS NULL OR document_id=p_document)
         AND (p_version IS NULL OR version_id=p_version)
         AND (p_job IS NULL OR job_id=p_job);
    END
    """
    # 4. 쓰기 작업용 회사 행 잠금. CALL한 연결의 트랜잭션이 끝날 때 풀린다.
    lock_company = f"""
    CREATE PROCEDURE company_lock_status() SQL SECURITY DEFINER
    BEGIN
      {guard}
      SELECT is_active FROM {control}.companies WHERE id='{company_id}' FOR UPDATE;
    END
    """
    # 5. 회사 정보 조회용 VIEW. 공통 companies 전체 대신 현재 회사의 허용 필드만 보인다.
    company_view = f"""
    CREATE SQL SECURITY DEFINER VIEW company_information AS
      SELECT id,name,is_active FROM {control}.companies WHERE id='{company_id}'
    """
    return [authenticate, insert_route, lookup_route, lock_company, company_view]


def _raise_routine_error(error):
    """DB 예외에서 오류 번호를 읽어 안전한 앱 예외를 발생시킨다. 반환값은 없다.

    orig는 SQLAlchemy가 감싼 원래 드라이버 예외다. args에 번호·메시지가
    없을 수도 있으므로 먼저 확인한다. 알 수 없는 메시지는 밖으로 내보내지 않는다.
    """
    original = getattr(error, 'orig', None)
    arguments = getattr(original, 'args', ())
    number = mysql_error_number(error)
    message = None
    if len(arguments) > 1:
        message = arguments[-1]
    # 1644는 프로시저의 SIGNAL로 발생한 오류다. 아래에 적힌 코드만 전달한다.
    if number == 1644 and message in ('ACCESS_DENIED', 'SESSION_EXPIRED'):
        raise AccessError(message) from None
    if number == 1644 and message == 'INVALID_STATE':
        raise DbError('INVALID_STATE') from None
    if number == 1062:
        # 1062는 고유 키 중복이다. SQL과 중복 값 대신 충돌 코드만 공개한다.
        raise DbError('CONFLICT') from None
    raise DbError('DB_UNAVAILABLE') from None


def authenticate_company_actor(database, actor, *, lock=False):
    """회사 연결에서 세션·회사 권한을 검증하고 현재 사용자 정보를 반환한다.

    lock=True이면 행 잠금은 호출자의 트랜잭션이 종료될 때까지 유지된다.
    입력 actor의 역할을 복사하지 않고 프로시저가 확인한 최신 DB 값을 사용한다.
    """
    from .clock import epoch_now
    from .sessions_repo import _session_credentials
    session_id, digest = _session_credentials(actor)
    try:
        # CALL은 앞에서 만든 SQL 프로시저의 실행이다. 값은 :이름 바인딩으로 전달한다.
        # mappings().one()은 필드 이름으로 읽을 수 있는 행을 정확히 한 개 요구한다.
        row = database.execute(text('CALL company_authenticate(:session,:digest,:now,:lock)'),
                               {'session': session_id, 'digest': digest, 'now': epoch_now(),
                                'lock': int(lock)}).mappings().one()
    except SQLAlchemyError as error:
        _raise_routine_error(error)
    current = dict(row)
    # 다음 DB 호출도 같은 세션을 증명할 수 있도록 서버 토큰만 원래 actor에서 보존한다.
    current['_session_token'] = actor['_session_token']
    return current


def register_company_route(database, document_id, version_id, job_id):
    """같은 트랜잭션의 회사 문서·버전·작업을 확인한 뒤 공통 라우팅을 등록한다.

    database는 호출자가 열어 둔 회사 연결이다. 별도 연결이나 commit을 만들지 않아
    라우팅 등록 실패 시 앞서 저장한 회사 문서·버전·작업도 함께 롤백할 수 있다.
    """
    values = {'document': require_id(document_id), 'version': require_id(version_id),
              'job': require_id(job_id)}
    try:
        database.execute(text('CALL company_register_route(:document,:version,:job)'), values)
    except SQLAlchemyError as error:
        _raise_routine_error(error)


def lookup_company_route(database, document_id=None, version_id=None, job_id=None):
    """회사 계정에서 자기 회사의 문서·버전·작업 라우팅을 조회한다."""
    values = {'document': document_id, 'version': version_id, 'job': job_id}
    try:
        rows = database.execute(text('CALL company_lookup_route(:document,:version,:job)'), values).scalars().all()
    except SQLAlchemyError as error:
        _raise_routine_error(error)
    if len(rows) != 1:
        raise DbError('INVALID_STATE')
    return rows[0]


def lock_company_status(database):
    """회사 행을 잠그고 현재 활성 상태를 반환한다."""
    try:
        return database.execute(text('CALL company_lock_status()')).scalar_one_or_none()
    except SQLAlchemyError as error:
        _raise_routine_error(error)
