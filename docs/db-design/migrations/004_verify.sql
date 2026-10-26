SELECT 'published_version' AS check_name, COUNT(*) AS violations
FROM documents d LEFT JOIN document_versions v
 ON v.id=d.active_version_id AND v.document_id=d.id AND v.company_id=d.company_id
WHERE d.publication_status='published' AND d.deleted_at IS NULL
 AND (v.id IS NULL OR v.version_status<>'ready' OR v.data_category<>'operational');

SELECT 'ready_manifest' AS check_name, COUNT(*) AS violations
FROM (
 SELECT v.id FROM document_versions v LEFT JOIN document_chunks ch ON ch.version_id=v.id
 WHERE v.version_status='ready'
 GROUP BY v.id,v.expected_chunk_count
 HAVING v.expected_chunk_count<=0 OR COUNT(ch.id)<>v.expected_chunk_count
    OR MIN(ch.chunk_index)<>0 OR MAX(ch.chunk_index)<>v.expected_chunk_count-1
) invalid_manifests;

SELECT 'document_company' AS check_name, COUNT(*) AS violations
FROM documents d LEFT JOIN companies c ON c.id=d.company_id WHERE c.id IS NULL;

SELECT 'version_document_company' AS check_name, COUNT(*) AS violations
FROM document_versions v LEFT JOIN documents d ON d.id=v.document_id AND d.company_id=v.company_id
WHERE d.id IS NULL;

SELECT 'active_identity' AS check_name, COUNT(*) AS violations
FROM documents d LEFT JOIN document_versions v
 ON v.id=d.active_version_id AND v.document_id=d.id AND v.company_id=d.company_id
WHERE d.active_version_id IS NOT NULL AND v.id IS NULL;
