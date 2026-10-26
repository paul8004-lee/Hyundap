-- ADMIN ONLY. Run db.schema --check successfully first, with application writes stopped.
-- This file does not apply DDL and must not be run after a partial/failed migration.
START TRANSACTION;
UPDATE schema_migrations SET version=4 WHERE version=3;
-- The administrator must verify ROW_COUNT() is exactly 1 before committing.
SELECT ROW_COUNT() AS changed_version_rows;
-- COMMIT is intentionally manual; ROLLBACK if changed_version_rows is not 1.
