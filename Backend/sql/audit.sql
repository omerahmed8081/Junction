-- The audit tables. Run once against the Aiven database:
--     psql "$DATABASE_URL" -f Backend/sql/audit.sql
--
-- Two tables, one per side, on purpose. The gateway's is what Aimo saw; the
-- kitchen's is why its agent did what it did. A single shared table would put
-- the kitchen's menu, stock and staff inside Aimo's system, which is the one
-- thing the architecture promises not to do. They join on quote_jti and order_id
-- when a dispute needs both halves.
--
-- Append-only by intention: insert, never update, never delete. In production
-- the application role gets INSERT and SELECT and nothing else, so a row cannot
-- be edited after the fact by the service that wrote it. The grants at the
-- bottom are commented out because the role name differs per environment.

CREATE SCHEMA IF NOT EXISTS gateway;
CREATE SCHEMA IF NOT EXISTS kitchen;

-- ---------------------------------------------------------------- gateway --
-- One row per call the gateway served. Written for every outcome, including
-- refusals: a refused order is the most interesting row in the table.
CREATE TABLE IF NOT EXISTS gateway.audit_log (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL DEFAULT now(),

    -- who called. The key itself is never stored, only a short hash of it.
    agent_id        text        NOT NULL,
    customer_id     text        NOT NULL,
    key_fingerprint text        NOT NULL DEFAULT '',

    -- what they asked for, and how it went
    action          text        NOT NULL,          -- search_products, create_quote, place_order, ...
    outcome         text        NOT NULL,          -- ok | refused | error
    status          integer     NOT NULL,          -- the HTTP status actually returned
    duration_ms     integer     NOT NULL DEFAULT 0,
    reason          text        NOT NULL DEFAULT '',   -- the refusal reason, in the gateway's own words
    detail          text        NOT NULL DEFAULT '',   -- the sentence a human reads first

    -- the full picture, for replaying what happened
    request         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    response        jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- what ties a row to the rest of the story, and to the kitchen's log
    quote_jti       text        NOT NULL DEFAULT '',
    order_id        text        NOT NULL DEFAULT '',

    -- a signature that did not verify, a mandate for another customer, a reused
    -- quote: the rows somebody should actually look at
    security_event  boolean     NOT NULL DEFAULT false
);

-- The Activity tab reads the newest rows for one customer.
CREATE INDEX IF NOT EXISTS audit_log_customer_at ON gateway.audit_log (customer_id, id DESC);
-- "Everything that happened to this order" and "...to this quote".
CREATE INDEX IF NOT EXISTS audit_log_order ON gateway.audit_log (order_id) WHERE order_id <> '';
CREATE INDEX IF NOT EXISTS audit_log_quote ON gateway.audit_log (quote_jti) WHERE quote_jti <> '';
-- "Show me only the things that look wrong."
CREATE INDEX IF NOT EXISTS audit_log_security ON gateway.audit_log (id DESC) WHERE security_event;

-- ---------------------------------------------------------------- kitchen --
-- One row per step the kitchen's agent took, so "why did my agent buy that"
-- has an answer. `crossed_gateway` is what drives the "what Aimo sees" toggle:
-- most of these rows never left the building.
CREATE TABLE IF NOT EXISTS kitchen.audit_log (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL DEFAULT now(),

    basket_id       text        NOT NULL DEFAULT '',
    actor           text        NOT NULL DEFAULT 'agent',  -- agent | human | schedule
    approver_role   text        NOT NULL DEFAULT '',       -- chef | manager | owner, when a person acted

    step            text        NOT NULL,          -- parse_need, search, convert, policy, approve, ...
    detail          text        NOT NULL DEFAULT '',
    reason          text        NOT NULL DEFAULT '',   -- why the code decided what it did

    -- the inputs and the result, so a decision can be replayed
    inputs          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    result          jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- which parser produced a need, when this row is a parse
    parser          text        NOT NULL DEFAULT '',

    quote_jti       text        NOT NULL DEFAULT '',
    order_id        text        NOT NULL DEFAULT '',
    crossed_gateway boolean     NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS kitchen_audit_basket ON kitchen.audit_log (basket_id, id DESC);
CREATE INDEX IF NOT EXISTS kitchen_audit_at ON kitchen.audit_log (id DESC);
CREATE INDEX IF NOT EXISTS kitchen_audit_order ON kitchen.audit_log (order_id) WHERE order_id <> '';

-- ------------------------------------------------------------------ grants --
-- Append-only in production: the service may add rows and read them, nothing more.
-- Replace app_user with the real role.
--
-- REVOKE UPDATE, DELETE, TRUNCATE ON gateway.audit_log, kitchen.audit_log FROM app_user;
-- GRANT INSERT, SELECT ON gateway.audit_log, kitchen.audit_log TO app_user;
-- GRANT USAGE, SELECT ON SEQUENCE gateway.audit_log_id_seq, kitchen.audit_log_id_seq TO app_user;
--
-- And in a real deployment the two schemas belong to two roles that cannot read
-- each other, which is what makes the separation above more than a convention.
