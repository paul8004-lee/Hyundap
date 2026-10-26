-- 직접 실행하는 SQL이 아닌 템플릿이다. db.schema --prepare가 기존 키의 정렬 규칙을 채운다.
CREATE TABLE document_versions (
    id __DOCUMENT_KEY__ NOT NULL PRIMARY KEY,
    document_id __DOCUMENT_KEY__ NOT NULL,
    company_id __COMPANY_KEY__ NOT NULL,
    version_no INT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    file_size BIGINT NOT NULL,
    version_status VARCHAR(16) NOT NULL DEFAULT 'processing',
    data_category VARCHAR(16) NOT NULL DEFAULT 'operational',
    expected_chunk_count INT NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_v4_version_number UNIQUE (document_id, version_no),
    CONSTRAINT uq_v4_version_identity UNIQUE (id, document_id, company_id),
    CONSTRAINT uq_v4_company_hash UNIQUE (company_id, sha256)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4;

CREATE TABLE document_chunks (
    id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    version_id __DOCUMENT_KEY__ NOT NULL,
    chunk_index INT NOT NULL,
    content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    CONSTRAINT uq_v4_chunk_order UNIQUE (version_id, chunk_index)
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4;
