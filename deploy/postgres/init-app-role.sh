#!/bin/sh
# Creates the runtime role the API/worker connect as. It is neither superuser nor
# BYPASSRLS, so PostgreSQL row-level security (migration 0001) applies to it.
# Migrations run as the owner role (POSTGRES_USER).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$POSTGRES_APP_PASSWORD" <<'SQL'
CREATE ROLE pmaip_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE pmaip TO pmaip_app;
GRANT USAGE ON SCHEMA public TO pmaip_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pmaip_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO pmaip_app;
SQL
