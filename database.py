import sqlite3
import json
import os
from datetime import datetime

class Database:
    DB_NAME = "bricklink_data.db"

    def __init__(self):
        # Create DB if not exists
        self.conn = sqlite3.connect(self.DB_NAME)
        self.cursor = self.conn.cursor()
        self._init_tables()

    def _init_tables(self):
        # Table for Items (Sets/Minifigs)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS items (
                item_id TEXT PRIMARY KEY,
                json_data TEXT,
                updated_at DATETIME
            )
        ''')
        
        # Table for Inventory Lists (Which minifigs are in which set)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory_lists (
                set_id TEXT PRIMARY KEY,
                json_data TEXT,
                updated_at DATETIME
            )
        ''')

        # Table for Collections (Separating ownership from data)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS collections (
                item_id TEXT,
                collection_name TEXT,
                added_at DATETIME,
                PRIMARY KEY (item_id, collection_name)
            )
        ''')
        self.conn.commit()

    def save_item(self, item_id, data):
        # Anti-Corruption Check: Don't overwrite valid data with empty/bad scrape results
        if self._is_empty_scrape(data):
            existing = self.get_item(item_id)
            if existing and not self._is_empty_scrape(existing):
                print(f"[DB Protection] 🛡️ Ignoring empty data update for {item_id}")
                return

        now = datetime.now().isoformat()
        json_str = json.dumps(data)
        self.cursor.execute('''
            INSERT OR REPLACE INTO items (item_id, json_data, updated_at)
            VALUES (?, ?, ?)
        ''', (item_id, json_str, now))
        self.conn.commit()

    def _is_empty_scrape(self, data):
        """Returns True if the data appears to be a failed/empty scrape."""
        try:
            return (
                not data.get("new", {}).get("sold") and 
                not data.get("new", {}).get("stock") and
                not data.get("used", {}).get("sold") and
                not data.get("used", {}).get("stock")
            )
        except:
            return True

    def get_item(self, item_id):
        self.cursor.execute('SELECT json_data, updated_at FROM items WHERE item_id = ?', (item_id,))
        row = self.cursor.fetchone()
        if row:
            data = json.loads(row[0])
            if "meta" in data:
                data["meta"]["cache_date"] = row[1]
            return data
        return None

    def save_inventory(self, set_id, data):
        now = datetime.now().isoformat()
        json_str = json.dumps(data)
        self.cursor.execute('''
            INSERT OR REPLACE INTO inventory_lists (set_id, json_data, updated_at)
            VALUES (?, ?, ?)
        ''', (set_id, json_str, now))
        self.conn.commit()

    def get_inventory(self, set_id):
        self.cursor.execute('SELECT json_data, updated_at FROM inventory_lists WHERE set_id = ?', (set_id,))
        row = self.cursor.fetchone()
        if row:
            return json.loads(row[0]), row[1]
        return None, None

    # --- COLLECTION METHODS ---
    def add_to_collection(self, item_id, collection_name):
        now = datetime.now().isoformat()
        self.cursor.execute('''
            INSERT OR IGNORE INTO collections (item_id, collection_name, added_at)
            VALUES (?, ?, ?)
        ''', (item_id, collection_name, now))
        self.conn.commit()

    def remove_from_collection(self, item_id, collection_name):
        self.cursor.execute('''
            DELETE FROM collections WHERE item_id = ? AND collection_name = ?
        ''', (item_id, collection_name))
        self.conn.commit()

    def get_collection_items(self, collection_name):
        self.cursor.execute('SELECT item_id FROM collections WHERE collection_name = ?', (collection_name,))
        return [row[0] for row in self.cursor.fetchall()]

    def close(self):
        self.conn.close()