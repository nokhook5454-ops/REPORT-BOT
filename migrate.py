"""Versioned PostgreSQL migrations, serialized across processes."""
import os
from pathlib import Path


def migrate(connect):
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(73190423)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())')
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=%s', (path.name,)).fetchone():
                conn.execute(path.read_text(encoding='utf-8'))
                conn.execute('INSERT INTO schema_migrations(version) VALUES (%s)', (path.name,))


if __name__ == '__main__':
    import psycopg
    migrate(lambda: psycopg.connect(os.environ['DATABASE_URL']))
    print('Migrations complete')
