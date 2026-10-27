-- 빈 DB 전용 최종 계약. 기존 DB의 마이그레이션 SQL로 실행하지 않는다.
-- 버전 행은 create_v4_schema가 전체 구조 검증 후 기록한다.
CREATE TABLE companies (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    updated_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    CONSTRAINT ck_company_name CHECK (CHAR_LENGTH(TRIM(name)) BETWEEN 1 AND 100),
    CONSTRAINT ck_company_active CHECK (is_active IN (0,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE users (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    company_id VARCHAR(36) NULL,
    email VARCHAR(254) NOT NULL,
    login_identifier VARCHAR(254) GENERATED ALWAYS AS (LOWER(TRIM(email))) STORED,
    google_sub VARCHAR(255) NULL,
    password_hash VARCHAR(255) NULL,
    role VARCHAR(32) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    auth_version INT NOT NULL DEFAULT 1,
    created_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    updated_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    CONSTRAINT uq_v4_login_identifier UNIQUE (login_identifier),
    CONSTRAINT uq_google_sub UNIQUE (google_sub),
    CONSTRAINT fk_user_company FOREIGN KEY (company_id) REFERENCES companies(id),
    CONSTRAINT ck_user_email CHECK (CHAR_LENGTH(TRIM(email)) BETWEEN 3 AND 254),
    CONSTRAINT ck_user_role CHECK (role IN ('platform_admin','company_admin','employee')),
    CONSTRAINT ck_user_company CHECK ((role='platform_admin' AND company_id IS NULL) OR (role IN ('company_admin','employee') AND company_id IS NOT NULL)),
    CONSTRAINT ck_user_active CHECK (is_active IN (0,1)),
    CONSTRAINT ck_user_auth_version CHECK (auth_version >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE documents (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    company_id VARCHAR(36) NOT NULL,
    doc_type VARCHAR(50) NOT NULL DEFAULT 'other',
    publication_status VARCHAR(16) NOT NULL DEFAULT 'draft',
    deleted_at DATETIME(6) NULL,
    active_version_id VARCHAR(36) NULL,
    uploaded_by VARCHAR(36) NOT NULL,
    created_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    updated_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    CONSTRAINT uq_v4_document_company UNIQUE (id,company_id),
    CONSTRAINT fk_v4_document_company FOREIGN KEY (company_id) REFERENCES companies(id),
    CONSTRAINT fk_document_uploader FOREIGN KEY (uploaded_by) REFERENCES users(id),
    CONSTRAINT ck_v4_publication CHECK (publication_status IN ('draft','published','blocked')),
    CONSTRAINT ck_v4_published_pointer CHECK (publication_status <> 'published' OR active_version_id IS NOT NULL),
    CONSTRAINT ck_v4_doc_type CHECK (doc_type IN ('manual','policy','guide','other'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE document_versions (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    company_id VARCHAR(36) NOT NULL,
    version_no INT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    file_size BIGINT NOT NULL,
    version_status VARCHAR(16) NOT NULL DEFAULT 'processing',
    data_category VARCHAR(16) NOT NULL DEFAULT 'operational',
    expected_chunk_count INT NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_v4_version_number UNIQUE (document_id,version_no),
    CONSTRAINT uq_v4_version_identity UNIQUE (id,document_id,company_id),
    CONSTRAINT uq_v4_company_hash UNIQUE (company_id,sha256),
    CONSTRAINT fk_v4_version_document FOREIGN KEY (document_id,company_id) REFERENCES documents(id,company_id),
    CONSTRAINT ck_v4_version_number CHECK (version_no >= 1),
    CONSTRAINT ck_v4_version_status CHECK (version_status IN ('processing','ready','failed')),
    CONSTRAINT ck_v4_data_category CHECK (data_category IN ('operational','evaluation')),
    CONSTRAINT ck_v4_file_size CHECK (file_size BETWEEN 1 AND 104857600),
    CONSTRAINT ck_v4_expected_count CHECK (expected_chunk_count BETWEEN 0 AND 100000),
    CONSTRAINT ck_v4_ready_count CHECK (version_status <> 'ready' OR expected_chunk_count > 0),
    CONSTRAINT ck_v4_sha256 CHECK (sha256 REGEXP '^[0-9a-f]{64}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

ALTER TABLE documents ADD CONSTRAINT fk_v4_active_version FOREIGN KEY (active_version_id,id,company_id) REFERENCES document_versions(id,document_id,company_id);

CREATE TABLE document_chunks (
    id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    version_id VARCHAR(36) NOT NULL,
    chunk_index INT NOT NULL,
    content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    CONSTRAINT uq_v4_chunk_order UNIQUE (version_id,chunk_index),
    CONSTRAINT fk_v4_chunk_version FOREIGN KEY (version_id) REFERENCES document_versions(id),
    CONSTRAINT ck_v4_chunk_index CHECK (chunk_index BETWEEN 0 AND 99999),
    CONSTRAINT ck_v4_chunk_id CHECK (id = CONCAT(version_id, ':', LPAD(chunk_index,5,'0'))),
    CONSTRAINT ck_v4_content_hash CHECK (content_hash REGEXP '^[0-9a-f]{64}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE index_jobs (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    version_id VARCHAR(36) NOT NULL,
    document_id VARCHAR(36) NOT NULL,
    company_id VARCHAR(36) NOT NULL,
    requested_by VARCHAR(36) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'QUEUED',
    stage VARCHAR(32) NOT NULL DEFAULT 'received',
    attempts INT NOT NULL DEFAULT 1,
    report JSON NULL,
    error_code VARCHAR(64) NULL,
    error_message VARCHAR(1000) NULL,
    started_at BIGINT NULL,
    finished_at BIGINT NULL,
    created_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    updated_at BIGINT NOT NULL DEFAULT (UNIX_TIMESTAMP()),
    active_document_id VARCHAR(36) GENERATED ALWAYS AS (CASE WHEN status IN ('QUEUED','RUNNING') THEN document_id ELSE NULL END) VIRTUAL,
    CONSTRAINT uq_job_version UNIQUE (version_id),
    CONSTRAINT uq_active_job UNIQUE (active_document_id),
    CONSTRAINT fk_job_version_identity FOREIGN KEY (version_id,document_id,company_id) REFERENCES document_versions(id,document_id,company_id),
    CONSTRAINT fk_job_requester FOREIGN KEY (requested_by) REFERENCES users(id),
    CONSTRAINT ck_job_attempts CHECK (attempts >= 1),
    CONSTRAINT ck_job_status CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
    CONSTRAINT ck_job_stage CHECK (stage IN ('received','ingest','embedding','verify','publish','complete')),
    CONSTRAINT ck_job_running CHECK (status <> 'RUNNING' OR started_at IS NOT NULL),
    CONSTRAINT ck_job_finished CHECK (status NOT IN ('SUCCEEDED','FAILED','CANCELLED') OR finished_at IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE sessions (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    csrf_token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    auth_version INT NOT NULL,
    selected_document_id VARCHAR(36) NULL,
    created_at BIGINT NOT NULL,
    last_seen_at BIGINT NOT NULL,
    idle_expires_at BIGINT NOT NULL,
    absolute_expires_at BIGINT NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    revoke_reason VARCHAR(255) NULL,
    CONSTRAINT uq_v4_session_token UNIQUE (token_hash),
    CONSTRAINT fk_session_user FOREIGN KEY (user_id) REFERENCES users(id),
    CONSTRAINT fk_session_document FOREIGN KEY (selected_document_id) REFERENCES documents(id) ON DELETE SET NULL,
    CONSTRAINT ck_session_token CHECK (token_hash REGEXP '^[0-9a-f]{64}$'),
    CONSTRAINT ck_session_csrf CHECK (csrf_token_hash REGEXP '^[0-9a-f]{64}$'),
    CONSTRAINT ck_session_auth CHECK (auth_version >= 1),
    CONSTRAINT sessions_ck_3 CHECK (created_at <= last_seen_at AND last_seen_at <= idle_expires_at AND idle_expires_at <= absolute_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE oauth_states (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    state_hash VARCHAR(64) NOT NULL,
    browser_binding_hash VARCHAR(64) NOT NULL,
    nonce_hash VARCHAR(64) NOT NULL,
    redirect_path LONGTEXT NOT NULL,
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    consumed_at BIGINT NULL,
    CONSTRAINT uq_state_hash UNIQUE (state_hash),
    CONSTRAINT ck_state_hash CHECK (CHAR_LENGTH(state_hash)=64),
    CONSTRAINT ck_binding_hash CHECK (CHAR_LENGTH(browser_binding_hash)=64),
    CONSTRAINT ck_nonce_hash CHECK (CHAR_LENGTH(nonce_hash)=64),
    CONSTRAINT ck_state_expiry CHECK (expires_at > created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE audit_logs (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    actor_user_id VARCHAR(36) NULL,
    company_id VARCHAR(36) NULL,
    document_id VARCHAR(36) NULL,
    action LONGTEXT NOT NULL,
    result VARCHAR(32) NOT NULL,
    request_id LONGTEXT NOT NULL,
    details_json LONGTEXT NOT NULL DEFAULT ('{}'),
    created_at BIGINT NOT NULL,
    CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(id),
    CONSTRAINT fk_audit_company FOREIGN KEY (company_id) REFERENCES companies(id),
    CONSTRAINT fk_audit_document FOREIGN KEY (document_id) REFERENCES documents(id),
    CONSTRAINT ck_audit_result CHECK (result IN ('SUCCESS','DENIED','FAILED')),
    INDEX ix_audit_company (company_id,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE login_attempts (
    key_hash VARCHAR(64) NOT NULL PRIMARY KEY,
    attempts BIGINT NOT NULL,
    window_started_at BIGINT NOT NULL,
    CONSTRAINT ck_login_attempts CHECK (attempts >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE schema_migrations (
    version BIGINT NOT NULL PRIMARY KEY,
    CONSTRAINT ck_current_version CHECK (version=4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
