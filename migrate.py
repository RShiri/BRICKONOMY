import os
import json
from database import Database

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def run_migration():
    if not os.path.exists(DATA_DIR):
        print("No data folder found. Starting fresh DB.")
        return

    db = Database()
    files = [f for f in os.listdir(DATA_DIR) if f.endswith('.json')]
    print(f"Found {len(files)} files to migrate...")

    count = 0
    for f in files:
        path = os.path.join(DATA_DIR, f)
        with open(path, 'r', encoding='utf-8') as file:
            try:
                data = json.load(file)
                
                # Check if it's an inventory file or item file
                if "_inventory.json" in f:
                    set_id = f.replace("_inventory.json", "")
                    db.save_inventory(set_id, data)
                else:
                    item_id = f.replace(".json", "")
                    db.save_item(item_id, data)
                
                count += 1
            except Exception as e:
                print(f"Skipping {f}: {e}")

    print(f"✅ Successfully migrated {count} items to SQLite.")
    db.close()

if __name__ == "__main__":
    run_migration()