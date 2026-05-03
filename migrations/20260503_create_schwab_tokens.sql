CREATE TABLE IF NOT EXISTS schwab_tokens (
    id SERIAL PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_type VARCHAR(64),
    expires_in INTEGER,
    scope TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
);
