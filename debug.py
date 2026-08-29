import sqlite3
import json
from pricing_engine import PriceAnalyzer

# מתחבר למסד הנתונים
conn = sqlite3.connect("bricklink_data.db")
cursor = conn.cursor()

# שולף פריט אחד לדוגמה
cursor.execute("SELECT item_id, json_data FROM items LIMIT 1")
row = cursor.fetchone()

if row:
    item_id = row[0]
    data = json.loads(row[1])
    
    print(f"🔍 Checking Item: {item_id}")
    
    # בדיקה 1: האם יש נתונים גולמיים?
    sold_count = len(data.get('new', {}).get('sold', []))
    stock_count = len(data.get('new', {}).get('stock', []))
    print(f"📊 Raw Data in DB -> Sold: {sold_count} | Stock: {stock_count}")
    
    if sold_count == 0 and stock_count == 0:
        print("❌ CRITICAL: The scraper saved EMPTY data. (BrickLink blocked you or page didn't load)")
    else:
        # בדיקה 2: האם המנוע מצליח לחשב?
        engine = PriceAnalyzer(data)
        res = engine.analyze()
        price = res['new']['market_price']
        print(f"💰 Calculated Price: {price}")
else:
    print("❌ Database is completely empty.")

conn.close()