-- CONTRACT v1: DB_contract.md adopted 2026-10-26. Runtime functions are #120-#122.
-- DESIGN ONLY: Not executed against the shared DB. Requires schema v4 first.
-- MySQL 8.4 / SQLAlchemy text() named parameters, not raw mysql CLI syntax.
-- Adopted physical names: documents.id, document_versions.id, documents.doc_type.
-- New version fields: id, document_id, company_id, version_no, version_status,
-- data_category, file_name, sha256, expected_chunk_count.
-- New chunk fields: id (Chroma chunk ID), version_id, chunk_index, content_hash.
-- User fields: password_hash, auth_version, generated login_identifier.
-- App roles are canonical lowercase; map legacy stored uppercase values at the boundary.
-- IN parameters require bindparam(name, expanding=True).
-- Empty document_ids => []; empty allowed_company_ids => access denied BEFORE SQL.
-- Authorization must be derived from fresh server-side DB state, never caller-supplied scope.

-- Q01 Login lookup: identifier is normalized email (strip + lowercase), max 254 chars.
-- Verify Argon2id in Python. Return no password_hash to UI. Parameterize all values.
SELECT id, company_id, role, is_active, password_hash, auth_version
FROM users WHERE login_identifier = :normalized_email LIMIT 1;

-- Q02 Company list: caller must verify actor is platform_admin before executing.
-- Other roles get only their own eligible company; never fall through to this query.
SELECT id, name FROM companies WHERE is_active = 1 ORDER BY id;

-- Q03 Duplicate candidates include deleted/failed versions. Retry the same failed version.
-- Before legacy import completes, block new uploads for that company (DB_contract.md).
-- This SELECT alone does not prevent concurrent duplicate INSERTs.
SELECT d.id AS document_id, v.id AS version_id,
       d.deleted_at, v.version_status
FROM documents d JOIN document_versions v
  ON v.document_id = d.id AND v.company_id = d.company_id
WHERE d.company_id = :company_id AND v.sha256 = :sha256;

-- Q04 FUN-247 DocumentInfo batch fetch; missing IDs are omitted.
-- Internal candidate metadata only; mandatory visibility/actor checks follow in Q05.
-- Alias physical names rather than forcing destructive PK renames.
SELECT d.id AS document_id, d.company_id, c.name AS company_name,
       d.doc_type, d.publication_status, d.deleted_at, d.active_version_id,
       v.version_no, v.version_status, v.data_category, v.file_name
FROM documents d JOIN companies c ON c.id = d.company_id
LEFT JOIN document_versions v
  ON v.id = d.active_version_id AND v.document_id = d.id
 AND v.company_id = d.company_id
WHERE d.id IN :document_ids;

-- Q05 One cited/search-hit pair access check; used in batch in production.
-- Bind :hit_company_id from vector hit, :hit_version_id from hit/citation.
-- In final recheck also revalidate actor/session/current role outside this statement.
SELECT d.id AS document_id, v.id AS version_id, c.name AS company_name,
       v.file_name, d.doc_type
FROM documents d JOIN companies c ON c.id = d.company_id
JOIN document_versions v
  ON v.id = d.active_version_id AND v.document_id = d.id
 AND v.company_id = d.company_id
WHERE d.id = :document_id
  AND d.company_id IN :allowed_company_ids
  AND d.company_id = :hit_company_id
  AND c.is_active = 1
  AND v.id = :hit_version_id
  AND d.publication_status = 'published' AND d.deleted_at IS NULL
  AND v.version_status = 'ready' AND v.data_category = 'operational';

-- Q06 Expected manifest for FUN-318 comparison with Chroma actual ID set.
-- Validate identity/scope/content as appropriate; count alone is insufficient.
SELECT id AS chunk_id, chunk_index, content_hash
FROM document_chunks WHERE version_id = :version_id ORDER BY chunk_index;

-- Q07 Candidate locking in a caller-owned short transaction.
-- Lock document first consistently in all publication paths, then candidate version.
SELECT id, company_id, active_version_id, publication_status, deleted_at
FROM documents WHERE id = :document_id FOR UPDATE;
SELECT id, document_id, company_id, version_status, data_category, expected_chunk_count
FROM document_versions
WHERE id = :candidate_id AND document_id = :document_id
  AND company_id = :company_id FOR UPDATE;
-- Revalidate actor, completeness and job state; P0 has one sequential worker.
-- Do not call Chroma under locks.
-- If eligibility is mutable, seal/version manifest and validate candidate generation.

-- Q08 CAS pointer switch, inside Q07 transaction after all eligibility checks.
-- MySQL <=> is NULL-safe equality. Verify exactly one changed row;
-- if candidate already active, handle idempotent success separately.
-- This does NOT atomically change Chroma flags. Coordinate staged activation/recovery.
UPDATE documents SET active_version_id = :candidate_id
WHERE id = :document_id AND company_id = :company_id
  AND deleted_at IS NULL AND active_version_id <=> :expected_active_id
  AND publication_status IN ('draft', 'published')
  AND EXISTS (
    SELECT 1 FROM document_versions v
    WHERE v.id = :candidate_id AND v.document_id = documents.id
      AND v.company_id = documents.company_id
      AND v.version_status = 'ready' AND v.data_category = 'operational'
  );
-- Audit INSERT and job state update belong to the same transaction.

-- Q09 P3 reference only, not a P0 implementation task. Caller locks actor and target,
-- authorizes company scope and role transitions, then writes audit in same TX.
-- auth_version increment requires session/checker implementation to consume it.
UPDATE users SET role = :new_role, auth_version = auth_version + 1
WHERE id = :target_user_id AND auth_version = :expected_auth_version;

-- Q10 Soft-delete primitive; :deleted_at must match the agreed timestamp type.
-- Caller authorizes actor, writes audit, and schedules external cleanup after commit.
UPDATE documents SET deleted_at = :deleted_at, publication_status = 'blocked'
WHERE id = :document_id AND company_id IN :allowed_company_ids
  AND deleted_at IS NULL;

-- Q11 Published document consistency audit (expected 0 rows).
SELECT d.id, d.company_id, d.active_version_id
FROM documents d LEFT JOIN document_versions v
  ON v.id = d.active_version_id AND v.document_id = d.id
 AND v.company_id = d.company_id
WHERE d.publication_status = 'published' AND d.deleted_at IS NULL
  AND (v.id IS NULL OR v.version_status <> 'ready'
       OR v.data_category <> 'operational');

-- Q12 Manifest count audit (does not replace DB-vs-Chroma set comparison).
SELECT v.id, v.expected_chunk_count, COUNT(ch.id) AS manifest_count
FROM document_versions v LEFT JOIN document_chunks ch ON ch.version_id = v.id
WHERE v.version_status = 'ready'
GROUP BY v.id, v.expected_chunk_count
HAVING v.expected_chunk_count IS NULL OR v.expected_chunk_count <= 0
    OR COUNT(ch.id) <> v.expected_chunk_count;

-- Creation/report INSERT implementation belongs to #121; adopted DTO/DDL is documented.
-- No broad audit subsystem is added in #118/#119.
-- Never silently apply this catalog to schema v3.
