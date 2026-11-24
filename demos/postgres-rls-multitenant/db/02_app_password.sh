#!/bin/sh
# Runs once at container init (after schema.sql): give app_user the password from the environment.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$APP_DB_PASSWORD" <<'SQL'
ALTER ROLE app_user PASSWORD :'pw';
SQL
