\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I LOGIN', :'reader_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reader_role')
\gexec

SELECT format(
    'ALTER ROLE %I LOGIN PASSWORD %L',
    :'reader_role',
    :'reader_password'
)
\gexec
SELECT format(
    'ALTER ROLE %I SET default_transaction_read_only = on',
    :'reader_role'
)
\gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'reader_role')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'reader_role')
\gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', :'reader_role')
\gexec

SELECT format('GRANT USAGE ON SCHEMA %I TO %I', nspname, :'reader_role')
FROM pg_namespace
WHERE nspname IN ('_timescaledb_catalog', '_timescaledb_internal', '_timescaledb_config')
\gexec

SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', nspname, :'reader_role')
FROM pg_namespace
WHERE nspname IN ('_timescaledb_catalog', '_timescaledb_internal', '_timescaledb_config')
\gexec
