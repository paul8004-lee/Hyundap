-- 관리자 전용. 앱의 쓰기를 멈추고 db.schema --check가 통과한 뒤에만 실행한다.
-- 이 파일은 DDL을 적용하지 않는다. 부분 적용·실패한 마이그레이션 뒤에는 실행하지 않는다.
START TRANSACTION;
UPDATE schema_migrations SET version=4 WHERE version=3;
-- 관리자는 커밋 전에 ROW_COUNT()가 정확히 1인지 확인해야 한다.
SELECT ROW_COUNT() AS changed_version_rows;
-- COMMIT은 관리자가 직접 실행한다. changed_version_rows가 1이 아니면 ROLLBACK한다.
