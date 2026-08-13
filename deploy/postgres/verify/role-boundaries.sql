\set ON_ERROR_STOP on

DO $$
DECLARE
    expected_role text;
BEGIN
    FOREACH expected_role IN ARRAY ARRAY[
        'authentik',
        'ai_hub_platform_migrator',
        'ai_hub_platform',
        'ai_hub_projection_migrator',
        'ai_hub_projection',
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
            'ai_hub_projection_migrator',
            'ai_hub_projection',
            'standalone_app_migrator',
            'standalone_app',
            'standalone_outbox_publisher',
            'standalone_event_consumer'
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
        'ai_hub_projection_migrator', 'platform_db', 'CONNECT'
    ) OR has_database_privilege(
        'ai_hub_projection_migrator', 'authentik_db', 'CONNECT'
    ) OR has_database_privilege(
        'ai_hub_projection_migrator', 'standalone_app_db', 'CONNECT'
    ) THEN
        RAISE EXCEPTION 'projection migrator database boundary is invalid';
    END IF;

    IF NOT has_database_privilege('ai_hub_projection', 'platform_db', 'CONNECT')
       OR has_database_privilege('ai_hub_projection', 'authentik_db', 'CONNECT')
       OR has_database_privilege('ai_hub_projection', 'standalone_app_db', 'CONNECT') THEN
        RAISE EXCEPTION 'projection runtime database boundary is invalid';
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

    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'standalone_outbox_publisher'
    ) THEN
        IF has_database_privilege(
            'standalone_outbox_publisher', 'authentik_db', 'CONNECT'
        ) OR has_database_privilege(
            'standalone_outbox_publisher', 'platform_db', 'CONNECT'
        ) THEN
            RAISE EXCEPTION 'Outbox publisher can connect outside its application database';
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'standalone_event_consumer'
    ) THEN
        IF has_database_privilege(
            'standalone_event_consumer', 'authentik_db', 'CONNECT'
        ) OR has_database_privilege(
            'standalone_event_consumer', 'platform_db', 'CONNECT'
        ) THEN
            RAISE EXCEPTION 'event consumer can connect outside its application database';
        END IF;
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
        WHERE nspname = 'platform_projection'
    ) IS DISTINCT FROM 'ai_hub_projection_migrator' THEN
        RAISE EXCEPTION 'platform_projection schema owner is invalid';
    END IF;

    IF NOT has_schema_privilege(
        'ai_hub_platform_migrator', 'platform_core', 'USAGE,CREATE'
    ) OR has_schema_privilege(
        'ai_hub_platform_migrator', 'platform_projection', 'USAGE'
    ) THEN
        RAISE EXCEPTION 'platform migrator schema boundary is invalid';
    END IF;

    IF NOT has_schema_privilege(
        'ai_hub_projection_migrator', 'platform_projection', 'USAGE,CREATE'
    ) OR has_schema_privilege(
        'ai_hub_projection_migrator', 'platform_core', 'USAGE'
    ) THEN
        RAISE EXCEPTION 'projection migrator schema boundary is invalid';
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

    IF has_schema_privilege('ai_hub_projection', 'platform_core', 'USAGE')
       OR has_table_privilege(
           'ai_hub_projection',
           'platform_core.alembic_version',
           'SELECT,INSERT,UPDATE,DELETE'
       ) THEN
        RAISE EXCEPTION 'projection runtime can access platform_core';
    END IF;

    IF to_regclass('platform_projection.integration_inbox') IS NOT NULL THEN
        IF NOT has_schema_privilege('ai_hub_platform', 'platform_projection', 'USAGE')
           OR NOT has_table_privilege(
               'ai_hub_platform',
               'platform_projection.integration_inbox',
               'SELECT'
           )
           OR has_table_privilege(
               'ai_hub_platform',
               'platform_projection.integration_inbox',
               'INSERT,UPDATE,DELETE'
           ) THEN
            RAISE EXCEPTION 'platform runtime projection privilege is not read-only';
        END IF;

        IF to_regclass('platform_projection.alembic_version') IS NULL THEN
            RAISE EXCEPTION 'platform projection migration metadata is missing';
        END IF;

        IF has_table_privilege(
            'ai_hub_platform',
            'platform_projection.alembic_version',
            'SELECT,INSERT,UPDATE,DELETE'
        ) THEN
            RAISE EXCEPTION 'platform runtime can access projection migration metadata';
        END IF;

        IF NOT has_schema_privilege('ai_hub_projection', 'platform_projection', 'USAGE')
           OR has_schema_privilege('ai_hub_projection', 'platform_projection', 'CREATE')
           OR NOT has_table_privilege(
               'ai_hub_projection', 'platform_projection.integration_inbox', 'SELECT'
           )
           OR NOT has_table_privilege(
               'ai_hub_projection', 'platform_projection.integration_inbox', 'INSERT'
           )
           OR NOT has_table_privilege(
               'ai_hub_projection', 'platform_projection.integration_inbox', 'UPDATE'
           )
           OR NOT has_table_privilege(
               'ai_hub_projection', 'platform_projection.integration_inbox', 'DELETE'
           ) THEN
            RAISE EXCEPTION 'projection runtime platform_projection privilege is invalid';
        END IF;

        IF (
            SELECT tableowner
            FROM pg_tables
            WHERE schemaname = 'platform_projection'
              AND tablename = 'integration_inbox'
        ) IS DISTINCT FROM 'ai_hub_projection_migrator' THEN
            RAISE EXCEPTION 'platform projection table owner is invalid';
        END IF;

        IF (
            SELECT tableowner
            FROM pg_tables
            WHERE schemaname = 'platform_projection'
              AND tablename = 'alembic_version'
        ) IS DISTINCT FROM 'ai_hub_projection_migrator' THEN
            RAISE EXCEPTION 'platform projection migration table owner is invalid';
        END IF;

        IF has_table_privilege(
            'ai_hub_projection',
            'platform_projection.alembic_version',
            'SELECT,INSERT,UPDATE,DELETE'
        ) THEN
            RAISE EXCEPTION 'projection runtime can modify migration metadata';
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
       OR has_schema_privilege('ai_hub_projection', 'app', 'USAGE')
       OR has_schema_privilege('authentik', 'app', 'USAGE') THEN
        RAISE EXCEPTION 'a platform role can access the standalone app schema';
    END IF;

    IF to_regclass('app.integration_outbox') IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'standalone_outbox_publisher'
        ) THEN
            RAISE EXCEPTION 'API-only database enables the Outbox publisher role';
        END IF;
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'standalone_outbox_publisher'
        ) THEN
            RAISE EXCEPTION 'EVENT_PUBLISHER database role is missing';
        END IF;

        IF NOT has_database_privilege(
            'standalone_outbox_publisher', 'standalone_app_db', 'CONNECT'
        ) OR NOT has_schema_privilege('standalone_outbox_publisher', 'app', 'USAGE')
           OR has_schema_privilege('standalone_outbox_publisher', 'app', 'CREATE')
           OR NOT has_table_privilege(
               'standalone_outbox_publisher', 'app.integration_outbox', 'SELECT'
           )
           OR has_table_privilege(
               'standalone_outbox_publisher', 'app.integration_outbox', 'INSERT,DELETE'
           )
           OR NOT has_column_privilege(
               'standalone_outbox_publisher', 'app.integration_outbox', 'status', 'UPDATE'
           )
           OR NOT has_column_privilege(
               'standalone_outbox_publisher', 'app.integration_outbox', 'attempts', 'UPDATE'
           )
           OR has_column_privilege(
               'standalone_outbox_publisher', 'app.integration_outbox', 'payload', 'UPDATE'
           )
           OR has_table_privilege(
               'standalone_outbox_publisher', 'app.example_record', 'SELECT,INSERT,UPDATE,DELETE'
           )
           OR has_table_privilege(
               'standalone_outbox_publisher', 'app.integration_source_state', 'SELECT,INSERT,UPDATE,DELETE'
           ) THEN
            RAISE EXCEPTION 'Outbox publisher database privilege is broader than its relay duties';
        END IF;

        IF has_table_privilege(
            'standalone_app', 'app.integration_outbox', 'SELECT,UPDATE,DELETE'
        ) OR has_table_privilege(
            'standalone_app', 'app.integration_source_state', 'INSERT,DELETE'
        ) OR has_column_privilege(
            'standalone_app', 'app.integration_source_state', 'application_id', 'UPDATE'
        ) OR NOT has_column_privilege(
            'standalone_app', 'app.integration_source_state', 'current_sequence', 'UPDATE'
        ) THEN
            RAISE EXCEPTION 'standalone API can modify event delivery state';
        END IF;
    END IF;

    IF to_regclass('app.integration_inbox') IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'standalone_event_consumer'
        ) THEN
            RAISE EXCEPTION 'non-consumer database enables the event consumer role';
        END IF;
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'standalone_event_consumer'
        ) OR NOT has_database_privilege(
            'standalone_event_consumer', 'standalone_app_db', 'CONNECT'
        ) OR NOT has_schema_privilege(
            'standalone_event_consumer', 'app', 'USAGE'
        ) OR has_schema_privilege(
            'standalone_event_consumer', 'app', 'CREATE'
        ) OR NOT has_table_privilege(
            'standalone_event_consumer', 'app.integration_inbox',
            'SELECT,INSERT,UPDATE'
        ) OR has_table_privilege(
            'standalone_event_consumer', 'app.integration_inbox', 'DELETE'
        ) OR NOT has_table_privilege(
            'standalone_event_consumer', 'app.integration_consumer_effect',
            'SELECT,INSERT'
        ) OR has_table_privilege(
            'standalone_event_consumer', 'app.integration_consumer_effect',
            'UPDATE,DELETE'
        ) OR has_table_privilege(
            'standalone_event_consumer', 'app.example_record',
            'SELECT,INSERT,UPDATE,DELETE'
        ) THEN
            RAISE EXCEPTION 'event consumer database privilege is broader than its duties';
        END IF;

        IF has_table_privilege(
            'standalone_app', 'app.integration_inbox',
            'SELECT,INSERT,UPDATE,DELETE'
        ) OR has_table_privilege(
            'standalone_app', 'app.integration_consumer_effect',
            'SELECT,INSERT,UPDATE,DELETE'
        ) THEN
            RAISE EXCEPTION 'standalone API can access event consumer state';
        END IF;
    END IF;
END
$$;

SELECT 'database role boundaries verified' AS result;
