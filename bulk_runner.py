import sys
import pandas as pd
import time
from datetime import datetime, timedelta
from scraper import BrickLinkScraper
from pricing_engine import PriceAnalyzer
import os

# --- VISUALS ---
try:
    from colorama import init, Fore, Style
    from tqdm import tqdm
    init(autoreset=True)
    HAS_VISUALS = True
except ImportError:
    HAS_VISUALS = False
    class MockColor:
        def __getattr__(self, _): return ""
    Fore = Style = MockColor()
    def tqdm(iterable, **kwargs): return iterable
    print("Note: 'colorama' or 'tqdm' not found.")

# Force UTF-8 for Windows Consoles
sys.stdout.reconfigure(encoding='utf-8')

def run_bulk_scan(csv_file, collection_name=None):
    if not os.path.exists(csv_file):
        print(f"❌ Error: File {csv_file} not found.")
        return

    # 1. טעינת הסטים מהקובץ
    print(f"📂 Loading sets from {csv_file}...")
    df = pd.read_csv(csv_file)
    
    # ניקוי ה-IDs (מוריד את ה-1- אם קיים)
    set_ids = df['Number'].str.split('-').str[0].tolist()
    total = len(set_ids)
    
    print(f"🚀 Found {total} sets. Starting bulk scan...\n")
    
    scraper = BrickLinkScraper()
    
    iterator = tqdm(set_ids, desc="Bulk Scan", unit="set") if HAS_VISUALS else set_ids

    for i, set_id in enumerate(iterator, 1):
        if not HAS_VISUALS: print(f"[{i}/{total}] 🔍 Processing Set: {set_id}")
        
        try:
            # 1. Smart Cache Check (Max 1 week old)
            cached_data = scraper.db.get_item(set_id)
            is_fresh = False
            
            if cached_data:
                ts_str = cached_data.get('meta', {}).get('timestamp')
                if ts_str:
                    try:
                        last_scan = datetime.fromisoformat(ts_str)
                        if datetime.now() - last_scan < timedelta(days=7):
                            is_fresh = True
                    except: pass
            
            if is_fresh:
                if not HAS_VISUALS: print(f"   💾 Data is fresh (<7 days). Loading from DB...")
                set_data = cached_data
                
                # IMPORTANT: Even if cached, ensure it's in the collection!
                if collection_name:
                    scraper.db.add_to_collection(set_id, collection_name)
                    if not HAS_VISUALS: print(f"   🏷️  Added to collection: {collection_name}")
            
            else:
                # 2. Fresh Scrape
                set_data = scraper.scrape(set_id, item_type='S')
                if "error" in set_data:
                    print(f"   ⚠️ Skipping set {set_id}: {set_data['error']}")
                    continue
                
                # Tag with Collection Name (New System)
                if collection_name:
                    scraper.db.add_to_collection(set_id, collection_name)
                    if not HAS_VISUALS: print(f"   🏷️ Added to collection: {collection_name}")
            
            # ב. ניתוח מחיר (להצגה בטרמינל)
            
            # ב. ניתוח מחיר (להצגה בטרמינל)
            analysis = PriceAnalyzer(set_data).analyze()
            price = analysis.get('new', {}).get('market_price', 0)
            if not HAS_VISUALS: print(f"   💰 Market Price: {price:.2f} ILS")
            
            # ג. מציאת דמויות וסריקת המחירים שלהן (אוטומטי!)
            minifigs = scraper.get_minifigs_in_set(set_id)
            if minifigs:
                if not HAS_VISUALS: print(f"   👥 Found {len(minifigs)} minifigs. Scoping prices...")
                
                mf_iter = tqdm(minifigs, desc=f"Figs for {set_id}", leave=False) if HAS_VISUALS else minifigs
                for mf in mf_iter:
                    mf_id = mf['id']
                    # סורק מחיר לכל דמות בנפרד
                    scraper.scrape(mf_id, item_type='M')
            
            print(f"   ✅ Done with {set_id}\n")
            
            # המתנה קטנה כדי לא להיחסם
            time.sleep(2)
            
        except Exception as e:
            print(f"   ❌ Error processing {set_id}: {e}")
            continue

    print("=======================================")
    print("🎊 BULK SCAN COMPLETED!")
    print("You can now open the dashboard to see the results.")
    print("=======================================")

if __name__ == "__main__":
    # וודא ששם הקובץ תואם לקובץ שהעלית
    run_bulk_scan("BrickEconomy-Sets(2).csv", collection_name="Ram's Collection")