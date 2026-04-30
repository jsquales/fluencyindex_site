CREATE TABLE IF NOT EXISTS game24_puzzles (
    id INTEGER PRIMARY KEY,
    variant VARCHAR(64) NOT NULL,
    difficulty VARCHAR(64) NOT NULL,
    style VARCHAR(64),
    n1_raw VARCHAR(64) NOT NULL,
    n2_raw VARCHAR(64) NOT NULL,
    n3_raw VARCHAR(64) NOT NULL,
    n4_raw VARCHAR(64) NOT NULL,
    source_sheet VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS game24_attempts (
    id INTEGER PRIMARY KEY,
    puzzle_id INTEGER NOT NULL REFERENCES game24_puzzles(id) ON DELETE RESTRICT,
    student_identifier VARCHAR(128),
    started_at TIMESTAMP NOT NULL,
    submitted_at TIMESTAMP NOT NULL,
    response_time_ms INTEGER NOT NULL,
    is_correct BOOLEAN NOT NULL,
    error_code VARCHAR(64),
    error_message TEXT,
    variant VARCHAR(64) NOT NULL,
    difficulty VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS game24_attempt_rows (
    id INTEGER PRIMARY KEY,
    attempt_id INTEGER NOT NULL REFERENCES game24_attempts(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL CHECK (row_number IN (1, 2, 3)),
    left_raw VARCHAR(64) NOT NULL,
    operator VARCHAR(64) NOT NULL,
    right_raw VARCHAR(64) NOT NULL,
    result_raw VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_game24_attempts_puzzle_id ON game24_attempts (puzzle_id);
CREATE INDEX IF NOT EXISTS ix_game24_attempt_rows_attempt_id ON game24_attempt_rows (attempt_id);
