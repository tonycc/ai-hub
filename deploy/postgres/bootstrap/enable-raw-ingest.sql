\set ON_ERROR_STOP on

\getenv raw_migrator_password AI_HUB_RAW_MIGRATOR_DB_PASSWORD
\getenv raw_password AI_HUB_RAW_DB_PASSWORD

SELECT 'CREATE ROLE ai_hub_raw_migrator '
       'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'ai_hub_raw_migrator'
) \gexec

SELECT 'CREATE ROLE ai_hub_raw '
       'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'ai_hub_raw'
) \gexec

ALTER ROLE ai_hub_raw_migrator LOGIN PASSWORD :'raw_migrator_password';
ALTER ROLE ai_hub_raw LOGIN PASSWORD :'raw_password';

GRANT CONNECT, TEMPORARY ON DATABASE platform_db TO ai_hub_raw_migrator;
GRANT CONNECT ON DATABASE platform_db TO ai_hub_raw;

\connect platform_db

CREATE SCHEMA IF NOT EXISTS platform_raw AUTHORIZATION ai_hub_raw_migrator;

REVOKE ALL ON SCHEMA platform_raw FROM PUBLIC;
GRANT USAGE ON SCHEMA platform_raw TO ai_hub_platform, ai_hub_raw;
REVOKE ALL ON SCHEMA platform_raw FROM ai_hub_platform_migrator;

ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_raw_migrator IN SCHEMA platform_raw
    GRANT SELECT ON TABLES TO ai_hub_platform;
ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_raw_migrator IN SCHEMA platform_raw
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_hub_raw;
ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_raw_migrator IN SCHEMA platform_raw
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ai_hub_raw;

ALTER ROLE ai_hub_platform IN DATABASE platform_db
    SET search_path TO platform_core, platform_raw, public;
ALTER ROLE ai_hub_raw_migrator IN DATABASE platform_db
    SET search_path TO platform_raw, public;
ALTER ROLE ai_hub_raw IN DATABASE platform_db
    SET search_path TO platform_raw, public;

SELECT 'platform_raw roles and schema enabled' AS result;
