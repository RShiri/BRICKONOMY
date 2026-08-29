import argparse
import sys
import csv
import os
import time
from scraper import BrickLinkScraper
from pricing_engine import PriceAnalyzer

# Force UTF-8 for Windows Consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# --- VISUALS SETUP ---
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
    print("Note: 'colorama' or 'tqdm' not found. Running in plain mode.")

# --- CSV SETUP ---
def init_csvs():
    if not os.path.exists("sets_report.csv"):
        with open("sets_report.csv", "w", newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(["ID", "Name", "Type", "Year", "Status", "Market Price", "Trend", "Minifigs Value", "Profit vs Figs", "POV Profit", "Rating"])
    
    if not os.path.exists("minifigs_report.csv"):
        with open("minifigs_report.csv", "w", newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(["Parent Set", "Minifig ID", "Name", "Qty", "Cond", "Unit Price", "Total Value"])

def append_set_csv(data):
    with open("sets_report.csv", "a", newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(data)

def append_minifig_csv(data):
    with open("minifigs_report.csv", "a", newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(data)

# --- DISPLAY ---
def print_basic_report(item_id, item_name, results, trend_info=None):
    cache_date = results['meta'].get('cache_date', 'Fresh Fetch')
    
    print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}BRICKLINK REPORT: {Fore.YELLOW}{item_id} - {item_name}{Style.RESET_ALL}")
    print(f"Last Updated: {cache_date}")
    if trend_info:
        print(f"Trend       : {trend_info}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    
    for condition in ["new", "used"]:
        res = results[condition]
        
        # Color coding confidence
        conf_color = Fore.RED
        if res['confidence'] == "HIGH": conf_color = Fore.GREEN
        elif res['confidence'] == "MEDIUM": conf_color = Fore.YELLOW

        print(f"\n--- {condition.upper()} ---")
        print(f"Market Price   : {Fore.GREEN}{res['market_price']:.2f} ILS{Style.RESET_ALL}")
        print(f"Typical Range  : {res['range'][0]:.2f} - {res['range'][1]:.2f} ILS")
        print(f"Confidence     : {conf_color}{res['confidence']}{Style.RESET_ALL}")
        print(f"Data Integrity : {res['stats']['sold']['final_count']} Sales | {res['stats']['stock']['final_count']} Listings")

def print_deep_dive(results):
    deep = results["deep_dive"]
    lifecycle = deep["lifecycle"]
    sniper = deep["sniper"]
    
    print(f"\n{Fore.CYAN}{'-'*70}{Style.RESET_ALL}")
    print(f"🔍 STEP 2: INVESTMENT ANALYSIS")
    print(f"{Fore.CYAN}{'-'*70}{Style.RESET_ALL}")
    print(f"📅 STATUS: {lifecycle['status']} (Released: {lifecycle['year']})")
    
    print(f"\n🎯 SNIPER OPPORTUNITY (New)")
    if sniper and sniper['rating'] != "NO LISTINGS":
        rating_color = Fore.GREEN if "GOOD" in sniper['rating'] or "EXCELLENT" in sniper['rating'] else Fore.WHITE
        if "IRRELEVANT" in sniper['rating']: rating_color = Fore.RED
        
        print(f"   Deal Rating      : {rating_color}{sniper['rating']}{Style.RESET_ALL}")
        print(f"   Cheapest Listing : {sniper['price']:.2f} ILS")
        print(f"   Potential Profit : {sniper['profit_abs']:.2f} ILS (Margin: {sniper['margin_pct']}%)")
    else:
        print("   No valid listings found.")

def print_part_out(results):
    po = results.get("part_out", {})
    if not po: return
    print(f"\n{Fore.CYAN}{'-'*70}{Style.RESET_ALL}")
    print(f"🧩 STEP 4: PART OUT VALUE (POV) ESTIMATOR")
    print(f"{Fore.CYAN}{'-'*70}{Style.RESET_ALL}")
    
    rating_color = Fore.GREEN if po['rating'] == "HIGH" else Fore.YELLOW if po['rating'] == "MEDIUM" else Fore.RED
    
    print(f"📊 SPECS: {po.get('parts_count', 0)} Parts | {po.get('minifigs_count', 0)} Minifigs | {po.get('weight_g', 0)}g")
    print(f"💰 RATIOS: PPP: {po['ppp']:.2f} ILS | PPG: {po['ppg']:.2f} ILS")
    print(f"🚀 PART OUT RATING: {rating_color}{po['rating']}{Style.RESET_ALL} ({po['reason']})")
    
    # Calculate Theoretical POV Profit
    market_price = results['new']['market_price']
    if market_price > 0:
        # Simple heuristic: Part Out Value usually 1.5x - 3x Set Price depending on PPP. 
        # But we don't have scraped Part Out Value from set page, we only have PPP logic.
        # User asked to SHOW profit. We can estimate or use the passed logic.
        # Check if 'pricing_engine' calculates a 'pov_value'. It currently just gives PPP/Rating.
        # We'll just show what we have.
        pass
        
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")

# --- PROCESSORS ---

def get_market_price(item_id, item_type, force):
    scraper = BrickLinkScraper()
    raw = scraper.scrape(item_id, item_type=item_type, force=force)
    if "error" in raw: return None, raw
    engine = PriceAnalyzer(raw)
    return engine.analyze(), raw

def analyze_set_minifigs_dry(set_id, minifig_list, force):
    total_new = 0.0
    total_used = 0.0
    
    cache_checker = BrickLinkScraper()

    print(f"   🔎 Calculating market values for {len(minifig_list)} figures...\n")
    print(f"   {'ID':<10} {'Name':<35} {'Qty':<5} {'New (ea)':<12} {'Used (ea)':<12}")
    print(f"   {'-'*10} {'-'*35} {'-'*5} {'-'*12} {'-'*12}")



    iterator = tqdm(minifig_list, desc="Minifigs", leave=False) if HAS_VISUALS else minifig_list

    for mf in iterator:
        source_label = "🌐 Web"
        if not force and cache_checker._is_cache_valid(mf['id']):
            source_label = "💾 Cache"
        
        sys.stdout.write(f"\r   ⏳ Processing {mf['id']}... [{source_label}]                     ")
        sys.stdout.flush()
        
        res, _ = get_market_price(mf['id'], 'M', force)
        
        if res:
            p_new = res['new']['market_price']
            p_used = res['used']['market_price']
            
            line_new = p_new * mf['qty']
            line_used = p_used * mf['qty']
            
            total_new += line_new
            total_used += line_used
            
            print(f"\r   {mf['id']:<10} {mf['name'][:35]:<35} {mf['qty']:<5} {p_new:<12.2f} {p_used:<12.2f}")
            
            append_minifig_csv([set_id, mf['id'], mf['name'], mf['qty'], "NEW", f"{p_new:.2f}", f"{line_new:.2f}"])
            append_minifig_csv([set_id, mf['id'], mf['name'], mf['qty'], "USED", f"{p_used:.2f}", f"{line_used:.2f}"])
        else:
            print(f"\r   ❌ Error fetching {mf['id']}                               ")

    return total_new, total_used

# --- MAIN ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("item_ids", nargs="+", help="List of Item IDs")
    parser.add_argument("--type", default="S", choices=["S", "M"])
    parser.add_argument("--force", action="store_true", help="Ignore cache and force fresh scrape")
    args = parser.parse_args()
    
    init_csvs()
    
    grand_total_new_mkt = 0
    grand_total_used_mkt = 0
    batch_summary = []

    pbar_items = tqdm(args.item_ids, desc="Processing Items", unit="item") if HAS_VISUALS and len(args.item_ids) > 1 else args.item_ids

    for i, current_id in enumerate(pbar_items):
        
        if not HAS_VISUALS and i > 0:
            print(f"\n\n{'#'*80}")
            print(f"{'#'*30}   NEXT ITEM   {'#'*35}")
            print(f"{'#'*80}\n")

        # Auto-detect logic
        itype = "S"
        if args.type.lower() in ["m", "minifig"]: 
            itype = "M"
        elif not current_id[0].isdigit():
            # print(f"   ✨ Auto-detected Minifigure ID for {current_id}. Switching type to 'M'.")
            itype = "M"

        # print(f"Preparing to fetch data for {current_id} ({itype})...")
        
        temp_scraper = BrickLinkScraper()
        
        # --- TREND ANALYSIS SETUP ---
        old_price = 0
        trend_str = ""
        try:
            old_item = temp_scraper.db.get_item(current_id)
            if old_item:
                old_engine = PriceAnalyzer(old_item)
                old_res = old_engine.analyze()
                old_price = old_res['new']['market_price']
        except:
            pass
        
        if not args.force and temp_scraper._is_cache_valid(current_id):
            if not HAS_VISUALS: print(f"   💾 Found data in cache. Loading...")
            pass
        else:
            if not HAS_VISUALS: print(f"   🌐 Data not in cache (or forced). Downloading from BrickLink...")
            pass

        try:
            results, raw = get_market_price(current_id, itype, args.force)
        except Exception as e:
            print(f"{Fore.RED}Error processing {current_id}: {e}{Style.RESET_ALL}")
            continue
        
        if not results:
            print(f"Error: {raw.get('error', 'Unknown Error')}")
            continue

        # Calculate Trend
        new_price = results['new']['market_price']
        if old_price > 0 and new_price > 0:
            diff = new_price - old_price
            pct = (diff / old_price) * 100
            symbol = "▲" if diff > 0 else "▼"
            color = Fore.GREEN if diff > 0 else Fore.RED
            trend_str = f"{color}{new_price:.2f} ILS {symbol} {abs(pct):.1f}% since last scan{Style.RESET_ALL}"
            
        grand_total_new_mkt += results['new']['market_price']
        grand_total_used_mkt += results['used']['market_price']

        meta = results["meta"]
        # Clear line for clean report if using tqdm
        if HAS_VISUALS and len(args.item_ids) > 1: print("\r" + " "*100 + "\r")
        
        print_basic_report(current_id, meta['item_name'], results, trend_str)
        print_deep_dive(results)
        
        # --- MINIFIGS LOGIC ---
        mf_val_new = 0.0
        
        if itype == 'S':
            print(f"\n{'-'*70}")
            print(f"👥 STEP 3: MINIFIGURE BREAKDOWN (AUTO)")
            print(f"{'-'*70}")
            
            scraper = BrickLinkScraper()
            minifig_list = scraper.get_minifigs_in_set(current_id, args.force)
            
            if minifig_list:
                print(f"   ✅ Found {len(minifig_list)} minifigures.")
                mf_val_new, mf_val_used = analyze_set_minifigs_dry(current_id, minifig_list, args.force)
                
                set_price_new = results['new']['market_price']
                set_price_used = results['used']['market_price']
                
                pct_new = (mf_val_new / set_price_new * 100) if set_price_new > 0 else 0
                pct_used = (mf_val_used / set_price_used * 100) if set_price_used > 0 else 0

                print(f"\n   📊 COMPARISON (Set vs Minifigs):")
                print(f"   {'Metric':<15} {'NEW':<15} {'USED':<15}")
                print(f"   {'-'*45}")
                print(f"   {'Set Price':<15} {set_price_new:<15.2f} {set_price_used:<15.2f}")
                print(f"   {'Figs Sum':<15} {mf_val_new:<15.2f} {mf_val_used:<15.2f}")
                print(f"   {'Figs % of Set':<15} {pct_new:<14.1f}% {pct_used:<14.1f}%")
                
                if pct_new > 80: 
                    print(f"   🔥 NEW: Strong Part-Out Candidate! (Figs > 80% of Set Price)")
                else: 
                    print(f"   ❄️ NEW: Value is mostly in the bricks.")
            else:
                # --- THIS IS THE FIX ---
                print("   🚫 No minifigures found in this set.")

        print_part_out(results)

        # Save CSV
        deep = results["deep_dive"]
        po = results.get("part_out", {})
        row = [
            meta.get("item_id"), meta.get("item_name"), itype, meta.get("year_released"),
            deep["lifecycle"]["status"], results["new"]["market_price"],
            trend_str if trend_str else "N/A",
            mf_val_new, (mf_val_new - results["new"]["market_price"]),
            "N/A", # POV Profit (Need calculation)
            deep["sniper"]["rating"]
        ]
        append_set_csv(row)
        
        # Add to Batch Summary
        batch_summary.append({
            "id": current_id,
            "name": meta.get("item_name", "Unknown"),
            "new": results["new"]["market_price"],
            "used": results["used"]["market_price"]
        })
        
        print(f"✅ Data for {current_id} saved to CSV.")

    if len(args.item_ids) > 1:
        print(f"\n{Fore.MAGENTA}{'='*70}{Style.RESET_ALL}")
        print(f"📊 BATCH SUMMARY")
        print(f"{Fore.MAGENTA}{'='*70}{Style.RESET_ALL}")
        print(f"{'ID':<10} {'Name':<35} {'NEW (ILS)':<12} {'USED (ILS)':<12}")
        print(f"{'-'*10} {'-'*35} {'-'*12} {'-'*12}")
        
        for item in batch_summary:
            print(f"{item['id']:<10} {item['name'][:35]:<35} {item['new']:<12.2f} {item['used']:<12.2f}")

        print(f"\n{Fore.MAGENTA}{'='*70}{Style.RESET_ALL}")
        print(f"🏆 GRAND TOTALS ({len(args.item_ids)} Items)")
        print(f"{'='*70}")
        print(f"NEW Condition  : {Fore.GREEN}{grand_total_new_mkt:.2f} ILS{Style.RESET_ALL}")
        print(f"USED Condition : {Fore.YELLOW}{grand_total_used_mkt:.2f} ILS{Style.RESET_ALL}")
        print(f"{Fore.MAGENTA}{'='*70}{Style.RESET_ALL}")

    print("\n🏁 All requested items processed.")

if __name__ == "__main__":
    main()