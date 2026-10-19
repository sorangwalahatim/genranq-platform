#!/usr/bin/env bash
# Creates the owner role (runs migrations, owns tables) and the RLS-restricted app role.
set -euo pipefail
DB=${DB_NAME:-genranq}
psql -v ON_ERROR_STOP=1 <<SQL
do \$\$ begin
  if not exists (select from pg_roles where rolname='genranq_owner') then create role genranq_owner login password '${OWNER_PW:-genranq_owner}'; end if;
  if not exists (select from pg_roles where rolname='genranq_app') then create role genranq_app login password '${APP_PW:-genranq_app}' nobypassrls; end if;
end \$\$;
SQL
psql -tc "select 1 from pg_database where datname='$DB'" | grep -q 1 || psql -c "create database $DB owner genranq_owner"
psql -d $DB -c "create extension if not exists pgcrypto; create extension if not exists citext;"
