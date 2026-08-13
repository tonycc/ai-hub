\set ON_ERROR_STOP on

\getenv publisher_password STANDALONE_PUBLISHER_DB_PASSWORD

SELECT 'CREATE ROLE standalone_outbox_publisher '
       'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'standalone_outbox_publisher'
) \gexec

ALTER ROLE standalone_outbox_publisher
    LOGIN PASSWORD :'publisher_password';
GRANT CONNECT ON DATABASE standalone_app_db TO standalone_outbox_publisher;
GRANT USAGE ON SCHEMA app TO standalone_outbox_publisher;
