import sqlite3
import datetime
db_path = '../../.forge/forge-v3.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()
now = datetime.datetime.utcnow().isoformat() + 'Z'
c.execute("INSERT OR IGNORE INTO applied_migrations (version, name, applied_at) VALUES (?, ?, ?)", (12, 'lanes.up', now))
conn.commit()
conn.close()
print("Marked migration 12 as applied")
