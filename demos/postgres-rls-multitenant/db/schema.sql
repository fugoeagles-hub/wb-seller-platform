-- Multi-tenant schema with row-level security (PostgreSQL 16).
--
-- Tenant = "account". Every tenant-owned table carries account_id, and every
-- policy compares it with the per-transaction setting app.current_account_id.
-- Fail closed: no setting -> the policy expression is NULL -> zero rows visible.

-- ---------------------------------------------------------------- roles ----
-- schema_owner owns the objects (it is NOT a superuser, so FORCE RLS applies to it).
-- app_user is what the application connects as: no superuser, no BYPASSRLS.
CREATE ROLE schema_owner NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE app_user LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
-- The password is set at container init from an env var (db/02_app_password.sh).

CREATE SCHEMA app AUTHORIZATION schema_owner;
GRANT USAGE ON SCHEMA app TO app_user;

SET ROLE schema_owner;

-- Current tenant, read from a transaction-local setting.
-- current_setting(..., true) returns NULL when never set, '' after a transaction
-- that used set_config(..., true) has ended; NULLIF folds both into NULL.
CREATE FUNCTION app.current_account_id() RETURNS bigint
LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT NULLIF(current_setting('app.current_account_id', true), '')::bigint $$;

-- --------------------------------------------------------------- tables ----
CREATE TABLE app.accounts (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.stores (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES app.accounts (id) ON DELETE CASCADE,
    name       text NOT NULL,
    -- target for the composite FK below: a child row cannot point at another tenant's store
    UNIQUE (account_id, id)
);

-- Who may do what inside an account. role is a preset label; perms holds explicit
-- per-key overrides ({"section.ads": false} beats the preset, true grants extra).
CREATE TABLE app.account_access (
    account_id bigint NOT NULL REFERENCES app.accounts (id) ON DELETE CASCADE,
    user_id    bigint NOT NULL,            -- identity comes from an external auth provider
    role       text   NOT NULL CHECK (role IN ('owner', 'manager', 'buyer', 'viewer')),
    perms      jsonb  NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(perms) = 'object'),
    PRIMARY KEY (account_id, user_id)
);

CREATE TABLE app.daily_sales (
    account_id bigint  NOT NULL,
    store_id   bigint  NOT NULL,
    day        date    NOT NULL,
    sku        text    NOT NULL,
    orders     integer NOT NULL CHECK (orders >= 0),
    revenue    numeric(14, 2) NOT NULL CHECK (revenue >= 0),
    PRIMARY KEY (account_id, store_id, day, sku),
    FOREIGN KEY (account_id, store_id) REFERENCES app.stores (account_id, id) ON DELETE CASCADE
);

-- ------------------------------------------------------------------ RLS ----
-- ENABLE turns policies on; FORCE makes them apply to the table owner as well.
ALTER TABLE app.accounts       ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.accounts       FORCE  ROW LEVEL SECURITY;
ALTER TABLE app.stores         ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.stores         FORCE  ROW LEVEL SECURITY;
ALTER TABLE app.account_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.account_access FORCE  ROW LEVEL SECURITY;
ALTER TABLE app.daily_sales    ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.daily_sales    FORCE  ROW LEVEL SECURITY;

-- (SELECT fn()) lets the planner evaluate the function once per query, not per row.
-- USING filters reads and targets of UPDATE/DELETE; WITH CHECK rejects writes
-- that would put a row into another tenant.
CREATE POLICY accounts_isolation ON app.accounts
    FOR ALL
    USING      (id = (SELECT app.current_account_id()))
    WITH CHECK (id = (SELECT app.current_account_id()));

CREATE POLICY stores_isolation ON app.stores
    FOR ALL
    USING      (account_id = (SELECT app.current_account_id()))
    WITH CHECK (account_id = (SELECT app.current_account_id()));

CREATE POLICY account_access_isolation ON app.account_access
    FOR ALL
    USING      (account_id = (SELECT app.current_account_id()))
    WITH CHECK (account_id = (SELECT app.current_account_id()));

CREATE POLICY daily_sales_isolation ON app.daily_sales
    FOR ALL
    USING      (account_id = (SELECT app.current_account_id()))
    WITH CHECK (account_id = (SELECT app.current_account_id()));

-- --------------------------------------------------------------- grants ----
-- Creating/deleting tenants is a provisioning job for a privileged role, not the app.
GRANT SELECT, UPDATE ON app.accounts TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.stores, app.account_access, app.daily_sales TO app_user;
GRANT EXECUTE ON FUNCTION app.current_account_id() TO app_user;

RESET ROLE;
