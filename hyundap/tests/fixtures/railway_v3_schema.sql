CREATE TABLE `audit_logs` (
  `id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `actor_user_id` varchar(36) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `company_id` varchar(36) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `document_id` varchar(36) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `action` longtext COLLATE utf8mb4_0900_bin NOT NULL,
  `result` varchar(32) COLLATE utf8mb4_0900_bin NOT NULL,
  `request_id` longtext COLLATE utf8mb4_0900_bin NOT NULL,
  `details_json` longtext COLLATE utf8mb4_0900_bin NOT NULL DEFAULT (_utf8mb4'{}'),
  `created_at` bigint NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_audit_company` (`company_id`,`created_at`),
  KEY `audit_logs_fk_0` (`document_id`),
  KEY `audit_logs_fk_2` (`actor_user_id`),
  CONSTRAINT `audit_logs_fk_0` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `audit_logs_fk_1` FOREIGN KEY (`company_id`) REFERENCES `companies` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `audit_logs_fk_2` FOREIGN KEY (`actor_user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `audit_logs_ck_1` CHECK ((`result` in (_utf8mb4'SUCCESS',_utf8mb4'DENIED',_utf8mb4'FAILED')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE `companies` (
  `id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `name` longtext COLLATE utf8mb4_0900_bin NOT NULL,
  `is_active` bigint NOT NULL DEFAULT '1',
  `created_at` bigint NOT NULL,
  `updated_at` bigint NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `companies_ck_1` CHECK ((char_length(trim(`name`)) between 1 and 100)),
  CONSTRAINT `companies_ck_2` CHECK ((`is_active` in (0,1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE `documents` (
  `id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `company_id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `filename` longtext COLLATE utf8mb4_0900_bin NOT NULL,
  `storage_key` varchar(512) COLLATE utf8mb4_0900_bin NOT NULL,
  `media_type` longtext COLLATE utf8mb4_0900_bin NOT NULL,
  `size_bytes` bigint NOT NULL,
  `sha256` varchar(64) COLLATE utf8mb4_0900_bin NOT NULL,
  `status` varchar(32) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'UPLOADED',
  `content_revision` bigint NOT NULL DEFAULT '1',
  `generation` bigint NOT NULL DEFAULT '0',
  `indexed_job_id` varchar(36) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `uploaded_by` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `error_code` longtext COLLATE utf8mb4_0900_bin,
  `error_message` longtext COLLATE utf8mb4_0900_bin,
  `deleted_at` bigint DEFAULT NULL,
  `created_at` bigint NOT NULL,
  `updated_at` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `documents_uq_1` (`id`,`company_id`),
  UNIQUE KEY `documents_uq_2` (`storage_key`),
  KEY `ix_doc_list` (`company_id`,`status`,`created_at` DESC),
  KEY `documents_fk_0` (`uploaded_by`),
  KEY `documents_fk_1` (`indexed_job_id`),
  CONSTRAINT `documents_fk_0` FOREIGN KEY (`uploaded_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `documents_fk_1` FOREIGN KEY (`indexed_job_id`) REFERENCES `index_jobs` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `documents_fk_2` FOREIGN KEY (`company_id`) REFERENCES `companies` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `documents_ck_1` CHECK ((`size_bytes` > 0)),
  CONSTRAINT `documents_ck_2` CHECK (((char_length(`sha256`) = 64) and regexp_like(`sha256`,_utf8mb4'^[0-9a-f]{64}$',_utf8mb4'c'))),
  CONSTRAINT `documents_ck_3` CHECK ((`status` in (_utf8mb4'UPLOADED',_utf8mb4'INDEXING',_utf8mb4'READY',_utf8mb4'FAILED',_utf8mb4'DELETING',_utf8mb4'DELETED'))),
  CONSTRAINT `documents_ck_4` CHECK ((`content_revision` >= 1)),
  CONSTRAINT `documents_ck_5` CHECK ((`generation` >= 0)),
  CONSTRAINT `documents_ck_6` CHECK (((`status` <> _utf8mb4'READY') or (`indexed_job_id` is not null))),
  CONSTRAINT `documents_ck_7` CHECK (((`status` <> _utf8mb4'DELETED') or (`deleted_at` is not null)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE `index_jobs` (
  `id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `document_id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `company_id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `generation` bigint NOT NULL,
  `content_revision` bigint NOT NULL,
  `status` varchar(32) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'QUEUED',
  `attempt_no` bigint NOT NULL,
  `requested_by` varchar(36) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `lease_token` longtext COLLATE utf8mb4_0900_bin,
  `lease_expires_at` bigint DEFAULT NULL,
  `heartbeat_at` bigint DEFAULT NULL,
  `started_at` bigint DEFAULT NULL,
  `finished_at` bigint DEFAULT NULL,
  `error_code` longtext COLLATE utf8mb4_0900_bin,
  `error_message` longtext COLLATE utf8mb4_0900_bin,
  `created_at` bigint NOT NULL,
  `updated_at` bigint NOT NULL,
  `active_document_id` varchar(36) COLLATE utf8mb4_0900_bin GENERATED ALWAYS AS ((case when (`status` in (_utf8mb4'QUEUED',_utf8mb4'RUNNING')) then `document_id` else NULL end)) VIRTUAL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `index_jobs_uq_3` (`document_id`,`generation`),
  UNIQUE KEY `uq_active_job` (`active_document_id`),
  KEY `ix_job_lease` (`status`,`lease_expires_at`),
  KEY `ix_job_queue` (`status`,`created_at`),
  KEY `index_jobs_fk_0` (`document_id`,`company_id`),
  KEY `index_jobs_fk_1` (`requested_by`),
  CONSTRAINT `index_jobs_fk_0` FOREIGN KEY (`document_id`, `company_id`) REFERENCES `documents` (`id`, `company_id`) ON DELETE RESTRICT,
  CONSTRAINT `index_jobs_fk_1` FOREIGN KEY (`requested_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `index_jobs_ck_1` CHECK ((`generation` >= 1)),
  CONSTRAINT `index_jobs_ck_2` CHECK ((`content_revision` >= 1)),
  CONSTRAINT `index_jobs_ck_3` CHECK ((`attempt_no` >= 1)),
  CONSTRAINT `index_jobs_ck_4` CHECK ((`status` in (_utf8mb4'QUEUED',_utf8mb4'RUNNING',_utf8mb4'SUCCEEDED',_utf8mb4'FAILED',_utf8mb4'CANCELLED'))),
  CONSTRAINT `index_jobs_ck_5` CHECK (((`status` <> _utf8mb4'RUNNING') or ((`lease_token` is not null) and (`lease_expires_at` is not null) and (`started_at` is not null)))),
  CONSTRAINT `index_jobs_ck_6` CHECK (((`status` not in (_utf8mb4'SUCCEEDED',_utf8mb4'FAILED',_utf8mb4'CANCELLED')) or (`finished_at` is not null)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE `login_attempts` (
  `key_hash` varchar(64) COLLATE utf8mb4_0900_bin NOT NULL,
  `attempts` bigint NOT NULL,
  `window_started_at` bigint NOT NULL,
  PRIMARY KEY (`key_hash`),
  CONSTRAINT `login_attempts_ck_1` CHECK ((`attempts` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE `oauth_states` (
  `id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `state_hash` varchar(64) COLLATE utf8mb4_0900_bin NOT NULL,
  `browser_binding_hash` varchar(64) COLLATE utf8mb4_0900_bin NOT NULL,
  `nonce_hash` varchar(64) COLLATE utf8mb4_0900_bin NOT NULL,
  `redirect_path` longtext COLLATE utf8mb4_0900_bin NOT NULL,
  `created_at` bigint NOT NULL,
  `expires_at` bigint NOT NULL,
  `consumed_at` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `oauth_states_uq_1` (`state_hash`),
  KEY `ix_state_expiry` (`expires_at`),
  CONSTRAINT `oauth_states_ck_1` CHECK ((char_length(`state_hash`) = 64)),
  CONSTRAINT `oauth_states_ck_2` CHECK ((char_length(`browser_binding_hash`) = 64)),
  CONSTRAINT `oauth_states_ck_3` CHECK ((char_length(`nonce_hash`) = 64)),
  CONSTRAINT `oauth_states_ck_4` CHECK ((`expires_at` > `created_at`))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE `schema_migrations` (
  `version` bigint NOT NULL,
  PRIMARY KEY (`version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `sessions` (
  `id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `token_hash` varchar(64) COLLATE utf8mb4_0900_bin NOT NULL,
  `user_id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `csrf_token_hash` varchar(64) COLLATE utf8mb4_0900_bin NOT NULL,
  `selected_document_id` varchar(36) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `created_at` bigint NOT NULL,
  `last_seen_at` bigint NOT NULL,
  `idle_expires_at` bigint NOT NULL,
  `absolute_expires_at` bigint NOT NULL,
  `revoked_at` bigint DEFAULT NULL,
  `revoke_reason` longtext COLLATE utf8mb4_0900_bin,
  PRIMARY KEY (`id`),
  UNIQUE KEY `sessions_uq_2` (`token_hash`),
  KEY `ix_session_expiry` (`absolute_expires_at`),
  KEY `ix_session_user` (`user_id`,`revoked_at`),
  KEY `sessions_fk_0` (`selected_document_id`),
  CONSTRAINT `sessions_fk_0` FOREIGN KEY (`selected_document_id`) REFERENCES `documents` (`id`) ON DELETE SET NULL,
  CONSTRAINT `sessions_fk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `sessions_ck_1` CHECK ((char_length(`token_hash`) = 64)),
  CONSTRAINT `sessions_ck_2` CHECK ((char_length(`csrf_token_hash`) = 64)),
  CONSTRAINT `sessions_ck_3` CHECK (((`created_at` <= `last_seen_at`) and (`last_seen_at` <= `idle_expires_at`) and (`idle_expires_at` <= `absolute_expires_at`)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE `users` (
  `id` varchar(36) COLLATE utf8mb4_0900_bin NOT NULL,
  `company_id` varchar(36) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `email` varchar(320) COLLATE utf8mb4_0900_bin NOT NULL,
  `google_sub` varchar(255) COLLATE utf8mb4_0900_bin DEFAULT NULL,
  `role` varchar(32) COLLATE utf8mb4_0900_bin NOT NULL,
  `is_active` bigint NOT NULL DEFAULT '1',
  `created_at` bigint NOT NULL,
  `updated_at` bigint NOT NULL,
  `password` varchar(128) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `users_uq_2` (`email`),
  UNIQUE KEY `users_uq_1` (`google_sub`),
  KEY `ix_user_company` (`company_id`,`role`,`is_active`),
  CONSTRAINT `users_fk_0` FOREIGN KEY (`company_id`) REFERENCES `companies` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `users_ck_1` CHECK ((cast(`email` as char charset binary) = cast(lower(trim(`email`)) as char charset binary))),
  CONSTRAINT `users_ck_2` CHECK ((char_length(`password`) <= 128)),
  CONSTRAINT `users_ck_3` CHECK ((`role` in (_utf8mb4'SUPER_ADMIN',_utf8mb4'COMPANY_ADMIN',_utf8mb4'EMPLOYEE'))),
  CONSTRAINT `users_ck_4` CHECK ((`is_active` in (0,1))),
  CONSTRAINT `users_ck_5` CHECK ((((`role` = _utf8mb4'SUPER_ADMIN') and (`company_id` is null)) or ((`role` in (_utf8mb4'COMPANY_ADMIN',_utf8mb4'EMPLOYEE')) and (`company_id` is not null))))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;