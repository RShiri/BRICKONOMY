import json
import statistics
from database import Database
from pricing_engine import PriceAnalyzer

def explain(item_id):
    print(f"--- Price Math for {item_id} (USED Condition) ---\n")
    
    # 1. Load Data
    db = Database()
    item = db.get_item(item_id)
    db.close()
    
    if not item:
        print("Item not found.")
        return

    # Use existing analyzer to get helper methods
    analyzer = PriceAnalyzer(item)
    
    # Focus on USED condition
    sold_raw = item.get("used", {}).get("sold", [])
    stock_raw = item.get("used", {}).get("stock", [])
    
    print(f"1. Initial Data")
    print(f"    - Sold Items: {len(sold_raw)}")
    print(f"    - Stock Items: {len(stock_raw)}")
    
    # 2. Strict Completeness Filter
    print(f"\n2. After 'Incomplete/Blacklist' Filter")
    
    sold_data = []
    stock_data = []

    print("    [REJECTED STOCK ITEMS]:")
    for x in stock_raw:
        if analyzer._is_strictly_complete(x):
            stock_data.append(x)
        else:
            print(f"    [REJECTED] {x['price']} ILS | Desc: {x.get('description', 'No Desc')}")

    sold_data = [x for x in sold_raw if analyzer._is_strictly_complete(x)]
    
    print(f"    - Sold: {len(sold_data)}")
    print(f"    - Stock: {len(stock_data)}")
    
    # 3. Global Median & Price Floor
    all_prices = [x['price'] for x in sold_data] + [x['price'] for x in stock_data]
    if not all_prices:
        print("No valid data points.")
        return

    global_median = statistics.median(all_prices)
    
    # Mirroring Engine Logic for 2025
    released_year = item.get("meta", {}).get("year_released")
    if released_year == 2025:
        price_floor = 1
        print(f"    - **2025 Set Detected**: Floor Override -> 1 ILS")
    else:
        price_floor = global_median * 0.60
    
    print(f"\n3. Price Floor Calculation")
    print(f"    - Global Median: {global_median:.2f} ILS")
    print(f"    - Floor: {price_floor:.2f} ILS")
    
    # 4. Filter below floor
    sold_floor = [x for x in sold_data if x['price'] >= price_floor]
    stock_floor = [x for x in stock_data if x['price'] >= price_floor]
    
    print(f"\n4. After Floor Filter")
    print(f"    - Sold: {len(sold_floor)} (Removed {len(sold_data) - len(sold_floor)})")
    print(f"    - Stock: {len(stock_floor)} (Removed {len(stock_data) - len(stock_floor)})")
    
    # 5. Process Datasets (IQR & Weighted Avg)
    print(f"\n5. Outlier & Bulk Filtering (Sold Data)")
    sold_final, sold_avg = process_debug(sold_floor, analyzer)
    
    print(f"\n6. Outlier & Bulk Filtering (Stock Data)")
    stock_final, stock_comp_avg = process_debug(stock_floor, analyzer, is_stock=True)
    
    # 7. Final Calculation
    sold_count = len(sold_final)
    print(f"\n7. Final Weighting")
    print(f"    - Sold Avg: {sold_avg:.2f} ILS (Count: {sold_count})")
    print(f"    - Stock Avg: {stock_comp_avg:.2f} ILS")
    
    market_price = 0
    formula = ""
    
    if sold_count >= 10:
        market_price = (sold_avg * 0.70) + (stock_comp_avg * 0.30)
        formula = "(Sold * 0.7) + (Stock * 0.3)"
    elif sold_count >= 1:
        market_price = sold_avg
        formula = "Sold Avg Only (>= 1 Sale)"
    else:
        market_price = stock_comp_avg
        formula = "Stock Only (Low Sales)"
        
    print(f"\n- Final Market Price: {market_price:.2f} ILS")
    print(f"    Formula Used: {formula}")

def process_debug(items, analyzer, is_stock=False):
    # Filter Bulk
    no_bulk = [x for x in items if x["qty"] <= analyzer.BULK_THRESHOLD]
    print(f"    - Original: {len(items)}")
    print(f"    - After Bulk Filter (Qty <= {analyzer.BULK_THRESHOLD}): {len(no_bulk)}")
    
    final = no_bulk
    
    # IQR Filtering
    if len(no_bulk) >= 5:
        vals = []
        for x in no_bulk: vals.extend([x["price"]] * x["qty"])
        if len(vals) >= 4:
            q1 = statistics.quantiles(vals, n=4)[0]
            q3 = statistics.quantiles(vals, n=4)[2]
            iqr = q3 - q1
            low = q1 - 1.5 * iqr
            high = q3 + 1.5 * iqr
            
            final = [x for x in no_bulk if low <= x["price"] <= high]
            print(f"    - IQR Filter: [{low:.2f}, {high:.2f}]")
            print(f"    - Removed Outliers: {len(no_bulk) - len(final)}")
    
    avg = 0
    if is_stock:
        # Stock uses "Competitive Price" logic
        avg = analyzer._get_competitive_stock_price(final)
        print(f"    - Competitive Stock Logic Applied (Lowest ~35%)")
    else:
        avg = analyzer._weighted_avg(final)
        
    print(f"    -> Calculated Avg: {avg:.2f} ILS")
    return final, avg

if __name__ == "__main__":
    explain("5002946")
