\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I LOGIN', :'reader_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reader_role')
\gexec

SELECT format(
    'ALTER ROLE %I LOGIN PASSWORD %L',
    :'reader_role',
    secret.value
)
FROM pg_temp.domeye_reader_secret AS secret
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

SELECT 'REVOKE CREATE ON SCHEMA info FROM PUBLIC'
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'info')
\gexec
SELECT format('GRANT USAGE ON SCHEMA info TO %I', :'reader_role')
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'info')
\gexec

SELECT format(
    'GRANT SELECT ON TABLE info.%I TO %I',
    relation.relname,
    :'reader_role'
)
FROM pg_class AS relation
JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'info'
  AND relation.relkind IN ('r', 'p', 'v', 'm')
  AND relation.relname NOT IN (
      'as_contact', 'quarantine', 'import_run',
      'source_record', 'legacy_record'
  )
  AND relation.relname !~ '^(source_record|legacy_record)_r[0-9]+$'
ORDER BY relation.relname
\gexec

SELECT format('GRANT USAGE ON SCHEMA %I TO %I', nspname, :'reader_role')
FROM pg_namespace
WHERE nspname IN ('_timescaledb_catalog', '_timescaledb_internal', '_timescaledb_config')
\gexec

SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', nspname, :'reader_role')
FROM pg_namespace
WHERE nspname IN ('_timescaledb_catalog', '_timescaledb_internal', '_timescaledb_config')
\gexec
