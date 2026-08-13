\set ON_ERROR_STOP on

\getenv consumer_password STANDALONE_CONSUMER_DB_PASSWORD

SELECT 'CREATE ROLE standalone_event_consumer '
       'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'standalone_event_consumer'
) \gexec

ALTER ROLE standalone_event_consumer
    LOGIN PASSWORD :'consumer_password';
GRANT CONNECT ON DATABASE standalone_app_db TO standalone_event_consumer;
GRANT USAGE ON SCHEMA app TO standalone_event_consumer;
