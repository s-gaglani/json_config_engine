"""
reset_db.py — wipes all tables by dropping and recreating the public schema.
Requires no superuser privilege — works with the app's own DB user.
Run from the project root: python scripts/reset_db.py
"""
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

DB_NAME  = "config_engine"
USER     = "config_engine"
PASSWORD = "msbc%123"
HOST     = "localhost"
PORT     = "5432"

conn = psycopg2.connect(
    dbname=DB_NAME,
    user=USER,
    password=PASSWORD,
    host=HOST,
    port=PORT,
)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()

print("Dropping public schema (all tables, indexes, sequences)...")
cur.execute("DROP SCHEMA public CASCADE;")
print("Recreating public schema...")
cur.execute("CREATE SCHEMA public;")
cur.execute(f"GRANT ALL ON SCHEMA public TO {USER};")
cur.execute("GRANT ALL ON SCHEMA public TO public;")
print("Schema reset complete.")

cur.close()
conn.close()
print("Done. Run 'python manage.py migrate' to apply migrations fresh.")
