import os

db_file = "bricklink_data.db"

if os.path.exists(db_file):
    try:
        os.remove(db_file)
        print(f"✅ Database '{db_file}' has been DELETED successfully.")
        print("You can now start fresh.")
    except Exception as e:
        print(f"❌ Error deleting file: {e}")
else:
    print(f"⚠️ File '{db_file}' does not exist. Already clean.")