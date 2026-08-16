\set ON_ERROR_STOP on

DO $$
DECLARE
    expected_role text;
BEGIN
    FOREACH expected_role IN ARRAY ARRAY[
        'authentik',
        'ai_hub_platform_migrator',
        'ai_hub_platform',
        'ai_hub_raw_migrator',
        'ai_hub_raw',
        'standalone_app_migrator',
        'standalone_app'
    ]
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = expected_role) THEN
            RAISE EXCEPTION 'required role % is missing', expected_role;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = ANY (ARRAY[
            'authentik',
            'ai_hub_platform_migrator',
            'ai_hub_platform',
            'ai_hub_raw_migrator',
            'ai_hub_raw',
            'standalone_app_migrator',
            'standalone_app'
        ])
          AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'an application role has administrative privileges';
    END IF;

    IF NOT has_database_privilege('authentik', 'authentik_db', 'CONNECT')
       OR has_database_privilege('authentik', 'platform_db', 'CONNECT')
       OR has_database_privilege('authentik', 'standalone_app_db', 'CONNECT') THEN
        RAISE EXCEPTION 'authentik database boundary is invalid';
    END IF;

    IF NOT has_database_privilege('ai_hub_platform', 'platform_db', 'CONNECT')
       OR has_database_privilege('ai_hub_platform', 'authentik_db', 'CONNECT')
       OR has_database_privilege('ai_hub_platform', 'standalone_app_db', 'CONNECT') THEN
        RAISE EXCEPTION 'platform runtime database boundary is invalid';
    END IF;

    IF NOT has_database_privilege(
        'ai_hub_platform_migrator', 'platform_db', 'CONNECT'
    ) OR has_database_privilege(
        'ai_hub_platform_migrator', 'authentik_db', 'CONNECT'
    ) OR has_database_privilege(
        'ai_hub_platform_migrator', 'standalone_app_db', 'CONNECT'
    ) THEN
        RAISE EXCEPTION 'platform migrator database boundary is invalid';
    END IF;

    IF NOT has_database_privilege(
        'ai_hub_raw_migrator', 'platform_db', 'CONNECT'
    ) OR has_database_privilege(
        'ai_hub_raw_migrator', 'authentik_db', 'CONNECT'
    ) OR has_database_privilege(
        'ai_hub_raw_migrator', 'standalone_app_db', 'CONNECT'
    ) THEN
        RAISE EXCEPTION 'raw migrator database boundary is invalid';
    END IF;

    IF NOT has_database_privilege('ai_hub_raw', 'platform_db', 'CONNECT')
       OR has_database_privilege('ai_hub_raw', 'authentik_db', 'CONNECT')
       OR has_database_privilege('ai_hub_raw', 'standalone_app_db', 'CONNECT') THEN
        RAISE EXCEPTION 'raw runtime database boundary is invalid';
    END IF;

    IF NOT has_database_privilege('standalone_app', 'standalone_app_db', 'CONNECT')
       OR has_database_privilege('standalone_app', 'authentik_db', 'CONNECT')
       OR has_database_privilege('standalone_app', 'platform_db', 'CONNECT') THEN
        RAISE EXCEPTION 'standalone runtime database boundary is invalid';
    END IF;

    IF NOT has_database_privilege(
        'standalone_app_migrator', 'standalone_app_db', 'CONNECT'
    ) OR has_database_privilege(
        'standalone_app_migrator', 'authentik_db', 'CONNECT'
    ) OR has_database_privilege(
        'standalone_app_migrator', 'platform_db', 'CONNECT'
    ) THEN
        RAISE EXCEPTION 'standalone migrator database boundary is invalid';
    END IF;
END
$$;

\connect platform_db

DO $$
BEGIN
    IF (
        SELECT pg_get_userbyid(nspowner)
        FROM pg_namespace
        WHERE nspname = 'platform_core'
    ) IS DISTINCT FROM 'ai_hub_platform_migrator' THEN
        RAISE EXCEPTION 'platform_core schema owner is invalid';
    END IF;

    IF (
        SELECT pg_get_userbyid(nspowner)
        FROM pg_namespace
        WHERE nspname = 'platform_raw'
    ) IS DISTINCT FROM 'ai_hub_raw_migrator' THEN
        RAISE EXCEPTION 'platform_raw schema owner is invalid';
    END IF;

    IF NOT has_schema_privilege(
        'ai_hub_platform_migrator', 'platform_core', 'USAGE,CREATE'
    ) OR has_schema_privilege(
        'ai_hub_platform_migrator', 'platform_raw', 'USAGE'
    ) THEN
        RAISE EXCEPTION 'platform migrator schema boundary is invalid';
    END IF;

    IF NOT has_schema_privilege(
        'ai_hub_raw_migrator', 'platform_raw', 'USAGE,CREATE'
    ) OR has_schema_privilege(
        'ai_hub_raw_migrator', 'platform_core', 'USAGE'
    ) THEN
        RAISE EXCEPTION 'raw migrator schema boundary is invalid';
    END IF;

    IF NOT has_schema_privilege('ai_hub_platform', 'platform_core', 'USAGE')
       OR has_schema_privilege('ai_hub_platform', 'platform_core', 'CREATE') THEN
        RAISE EXCEPTION 'platform runtime platform_core schema privilege is invalid';
    END IF;

    IF (
        SELECT tableowner
        FROM pg_tables
        WHERE schemaname = 'platform_core'
          AND tablename = 'alembic_version'
    ) IS DISTINCT FROM 'ai_hub_platform_migrator' THEN
        RAISE EXCEPTION 'platform core migration table owner is invalid';
    END IF;

    IF (
        SELECT count(DISTINCT privilege_type)
        FROM pg_default_acl default_acl
        CROSS JOIN LATERAL aclexplode(default_acl.defaclacl) privilege
        JOIN pg_namespace namespace
          ON namespace.oid = default_acl.defaclnamespace
        WHERE default_acl.defaclrole = 'ai_hub_platform_migrator'::regrole
          AND default_acl.defaclobjtype = 'r'
          AND namespace.nspname = 'platform_core'
          AND privilege.grantee = 'ai_hub_platform'::regrole
          AND privilege.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
    ) <> 4 THEN
        RAISE EXCEPTION 'platform core default table privileges are invalid';
    END IF;

    IF has_table_privilege(
        'ai_hub_platform',
        'platform_core.alembic_version',
        'SELECT,INSERT,UPDATE,DELETE'
    ) THEN
        RAISE EXCEPTION 'platform runtime can modify core migration metadata';
    END IF;

    IF NOT has_table_privilege(
        'ai_hub_platform', 'platform_core.audit_event', 'INSERT'
    ) OR NOT has_table_privilege(
        'ai_hub_platform', 'platform_core.audit_event', 'SELECT'
    ) OR has_table_privilege(
        'ai_hub_platform', 'platform_core.audit_event', 'UPDATE'
    ) OR has_table_privilege(
        'ai_hub_platform', 'platform_core.audit_event', 'DELETE'
    ) THEN
        RAISE EXCEPTION 'platform audit table is not append-only and queryable for runtime';
    END IF;

    -- Raw runtime reads portal-managed ingest config from platform_core
    -- (design §2.5.1): USAGE on the schema plus SELECT on the two config tables
    -- only. It must not read or write any other core table or migration metadata.
    IF NOT has_schema_privilege('ai_hub_raw', 'platform_core', 'USAGE')
       OR has_schema_privilege('ai_hub_raw', 'platform_core', 'CREATE') THEN
        RAISE EXCEPTION 'raw runtime platform_core schema privilege is invalid';
    END IF;

    IF NOT has_table_privilege('ai_hub_raw', 'platform_core.ingest_source', 'SELECT')
       OR NOT has_table_privilege('ai_hub_raw', 'platform_core.ingest_policy', 'SELECT')
       OR has_table_privilege('ai_hub_raw', 'platform_core.ingest_source', 'INSERT,UPDATE,DELETE')
       OR has_table_privilege('ai_hub_raw', 'platform_core.ingest_policy', 'INSERT,UPDATE,DELETE') THEN
        RAISE EXCEPTION 'raw runtime ingest config privilege is not read-only';
    END IF;

    IF has_table_privilege(
           'ai_hub_raw',
           'platform_core.alembic_version',
           'SELECT,INSERT,UPDATE,DELETE'
       ) THEN
        RAISE EXCEPTION 'raw runtime can access core migration metadata';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_tables
        WHERE schemaname = 'platform_core'
          AND tablename NOT IN ('ingest_source', 'ingest_policy')
          AND has_table_privilege(
              'ai_hub_raw',
              format('%I.%I', schemaname, tablename),
              'SELECT,INSERT,UPDATE,DELETE'
          )
    ) THEN
        RAISE EXCEPTION 'raw runtime can access platform_core data beyond ingest config';
    END IF;

    IF to_regclass('platform_raw.raw_change_record') IS NOT NULL THEN
        IF NOT has_schema_privilege('ai_hub_platform', 'platform_raw', 'USAGE')
           OR NOT has_table_privilege(
               'ai_hub_platform',
               'platform_raw.raw_change_record',
               'SELECT'
           )
           OR has_table_privilege(
               'ai_hub_platform',
               'platform_raw.raw_change_record',
               'INSERT,UPDATE,DELETE'
           ) THEN
            RAISE EXCEPTION 'platform runtime raw privilege is not read-only';
        END IF;

        IF to_regclass('platform_raw.alembic_version') IS NULL THEN
            RAISE EXCEPTION 'platform raw migration metadata is missing';
        END IF;

        IF has_table_privilege(
            'ai_hub_platform',
            'platform_raw.alembic_version',
            'SELECT,INSERT,UPDATE,DELETE'
        ) THEN
            RAISE EXCEPTION 'platform runtime can access raw migration metadata';
        END IF;

        IF NOT has_schema_privilege('ai_hub_raw', 'platform_raw', 'USAGE')
           OR has_schema_privilege('ai_hub_raw', 'platform_raw', 'CREATE')
           OR NOT has_table_privilege(
               'ai_hub_raw', 'platform_raw.raw_change_record', 'SELECT'
           )
           OR NOT has_table_privilege(
               'ai_hub_raw', 'platform_raw.raw_change_record', 'INSERT'
           )
           OR NOT has_table_privilege(
               'ai_hub_raw', 'platform_raw.raw_change_record', 'UPDATE'
           )
           OR NOT has_table_privilege(
               'ai_hub_raw', 'platform_raw.raw_change_record', 'DELETE'
           )
           OR NOT has_table_privilege(
               'ai_hub_raw', 'platform_raw.raw_current_state', 'SELECT,INSERT,UPDATE,DELETE'
           )
           OR NOT has_table_privilege(
               'ai_hub_raw', 'platform_raw.raw_sync_cursor', 'SELECT,INSERT,UPDATE,DELETE'
           )
           OR NOT has_table_privilege(
               'ai_hub_raw', 'platform_raw.raw_ingest_batch', 'SELECT,INSERT,UPDATE,DELETE'
           ) THEN
            RAISE EXCEPTION 'raw runtime platform_raw privilege is invalid';
        END IF;

        IF (
            SELECT tableowner
            FROM pg_tables
            WHERE schemaname = 'platform_raw'
              AND tablename = 'raw_change_record'
        ) IS DISTINCT FROM 'ai_hub_raw_migrator' THEN
            RAISE EXCEPTION 'platform raw table owner is invalid';
        END IF;

        IF (
            SELECT tableowner
            FROM pg_tables
            WHERE schemaname = 'platform_raw'
              AND tablename = 'alembic_version'
        ) IS DISTINCT FROM 'ai_hub_raw_migrator' THEN
            RAISE EXCEPTION 'platform raw migration table owner is invalid';
        END IF;

        IF has_table_privilege(
            'ai_hub_raw',
            'platform_raw.alembic_version',
            'SELECT,INSERT,UPDATE,DELETE'
        ) THEN
            RAISE EXCEPTION 'raw runtime can modify migration metadata';
        END IF;
    END IF;
END
$$;

\connect standalone_app_db

DO $$
BEGIN
    IF NOT has_schema_privilege('standalone_app', 'app', 'USAGE')
       OR has_schema_privilege('standalone_app', 'app', 'CREATE') THEN
        RAISE EXCEPTION 'standalone runtime app schema privilege is invalid';
    END IF;

    IF NOT has_table_privilege('standalone_app', 'app.example_record', 'SELECT')
       OR NOT has_table_privilege('standalone_app', 'app.example_record', 'INSERT')
       OR NOT has_table_privilege('standalone_app', 'app.example_record', 'UPDATE')
       OR NOT has_table_privilege('standalone_app', 'app.example_record', 'DELETE') THEN
        RAISE EXCEPTION 'standalone runtime cannot use app tables';
    END IF;

    IF has_schema_privilege('ai_hub_platform', 'app', 'USAGE')
       OR has_schema_privilege('ai_hub_raw', 'app', 'USAGE')
       OR has_schema_privilege('authentik', 'app', 'USAGE') THEN
        RAISE EXCEPTION 'a platform role can access the standalone app schema';
    END IF;
END
$$;

SELECT 'database role boundaries verified' AS result;
