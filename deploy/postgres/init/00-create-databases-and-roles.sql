\set ON_ERROR_STOP on

\getenv authentik_password AUTHENTIK_DB_PASSWORD
\getenv platform_migrator_password AI_HUB_PLATFORM_MIGRATOR_DB_PASSWORD
\getenv platform_password AI_HUB_PLATFORM_DB_PASSWORD
\getenv raw_migrator_password AI_HUB_RAW_MIGRATOR_DB_PASSWORD
\getenv raw_password AI_HUB_RAW_DB_PASSWORD
\getenv standalone_migrator_password STANDALONE_MIGRATOR_DB_PASSWORD
\getenv standalone_password STANDALONE_APP_DB_PASSWORD

CREATE ROLE authentik
    LOGIN PASSWORD :'authentik_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE ROLE ai_hub_platform_migrator
    LOGIN PASSWORD :'platform_migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE ROLE ai_hub_platform
    LOGIN PASSWORD :'platform_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE ROLE ai_hub_raw_migrator
    LOGIN PASSWORD :'raw_migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE ROLE ai_hub_raw
    LOGIN PASSWORD :'raw_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE ROLE standalone_app_migrator
    LOGIN PASSWORD :'standalone_migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE ROLE standalone_app
    LOGIN PASSWORD :'standalone_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

CREATE DATABASE authentik_db OWNER authentik TEMPLATE template0 ENCODING 'UTF8';
CREATE DATABASE platform_db OWNER ai_hub_platform_migrator TEMPLATE template0 ENCODING 'UTF8';
CREATE DATABASE standalone_app_db OWNER standalone_app_migrator TEMPLATE template0 ENCODING 'UTF8';

REVOKE ALL ON DATABASE authentik_db FROM PUBLIC;
REVOKE ALL ON DATABASE platform_db FROM PUBLIC;
REVOKE ALL ON DATABASE standalone_app_db FROM PUBLIC;

GRANT CONNECT, TEMPORARY ON DATABASE authentik_db TO authentik;
GRANT CONNECT, TEMPORARY ON DATABASE platform_db TO ai_hub_platform_migrator;
GRANT CONNECT, TEMPORARY ON DATABASE platform_db TO ai_hub_raw_migrator;
GRANT CONNECT ON DATABASE platform_db TO ai_hub_platform, ai_hub_raw;
GRANT CONNECT, TEMPORARY ON DATABASE standalone_app_db TO standalone_app_migrator;
GRANT CONNECT ON DATABASE standalone_app_db TO standalone_app;

\connect authentik_db

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO authentik;

\connect platform_db

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA platform_core AUTHORIZATION ai_hub_platform_migrator;
CREATE SCHEMA platform_raw AUTHORIZATION ai_hub_raw_migrator;

REVOKE ALL ON SCHEMA platform_core FROM PUBLIC;
REVOKE ALL ON SCHEMA platform_raw FROM PUBLIC;

GRANT USAGE ON SCHEMA platform_core TO ai_hub_platform;
GRANT USAGE ON SCHEMA platform_raw TO ai_hub_platform, ai_hub_raw;
REVOKE ALL ON SCHEMA platform_core FROM ai_hub_raw_migrator, ai_hub_raw;
REVOKE ALL ON SCHEMA platform_raw FROM ai_hub_platform_migrator;

ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_platform_migrator IN SCHEMA platform_core
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_hub_platform;
ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_platform_migrator IN SCHEMA platform_core
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ai_hub_platform;

ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_raw_migrator IN SCHEMA platform_raw
    GRANT SELECT ON TABLES TO ai_hub_platform;
ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_raw_migrator IN SCHEMA platform_raw
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_hub_raw;
ALTER DEFAULT PRIVILEGES FOR ROLE ai_hub_raw_migrator IN SCHEMA platform_raw
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ai_hub_raw;

ALTER ROLE ai_hub_platform_migrator IN DATABASE platform_db
    SET search_path TO platform_core, public;
ALTER ROLE ai_hub_platform IN DATABASE platform_db
    SET search_path TO platform_core, platform_raw, public;
ALTER ROLE ai_hub_raw_migrator IN DATABASE platform_db
    SET search_path TO platform_raw, public;
ALTER ROLE ai_hub_raw IN DATABASE platform_db
    SET search_path TO platform_raw, public;

\connect standalone_app_db

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA app AUTHORIZATION standalone_app_migrator;
REVOKE ALL ON SCHEMA app FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO standalone_app;

ALTER DEFAULT PRIVILEGES FOR ROLE standalone_app_migrator IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO standalone_app;
ALTER DEFAULT PRIVILEGES FOR ROLE standalone_app_migrator IN SCHEMA app
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO standalone_app;

ALTER ROLE standalone_app IN DATABASE standalone_app_db SET search_path TO app, public;
