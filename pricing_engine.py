import logging
import re
import statistics
from datetime import datetime
from typing import Any, Dict, List


class PriceAnalyzer:
    """
    Analyzes scraped marketplace data to calculate market prices and assess
    investment potential. Multi-layer filtering excludes incomplete sets and
    statistical outliers before any price is computed.
    """
    BULK_THRESHOLD = 3

    BLACKLIST = [
        'incomplete', 'missing', 'no minifig', 'no minifigs', 'no figure',
        'no figs', 'no box', 'no instructions', 'no manual', 'only build',
        'build only', 'just build', 'instruction only', 'without minifig',
        'without minifigures', 'figures removed', 'minifigures removed',
        'no figures', 'no mf', 'no character', 'no-minifig', 'no-minifigures',
        '(i)', 'missing parts', 'partially complete', 'only castle', 'no characters'
    ]

    # "No/without <thing>" and "<thing> only" phrasings the plain blocklist misses.
    RED_FLAG_PATTERNS = [
        r"\b(no|without)\b.{0,15}\b(minifig|figure|cat|manual|box)\b",
        r"\b(build|castle|vehicle)\b.{0,15}\b(only)\b",
    ]

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.meta = data.get("meta", {})

    def _is_strictly_complete(self, item: Dict) -> bool:
        """Layer 1: is this listing a complete set/figure, per its description?"""
        if item.get('status') == 'incomplete':
            return False

        text_to_scan = (str(item) + " " + item.get('description', '')).lower()

        for word in self.BLACKLIST:
            if word in text_to_scan:
                return False

        for pattern in self.RED_FLAG_PATTERNS:
            if re.search(pattern, text_to_scan):
                return False

        if 'only' in text_to_scan:
            if any(x in text_to_scan for x in ['build', 'instruction', 'parts']):
                return False

        return True

    def analyze(self, minifig_value_new: float = 0.0,
                minifig_value_used: float = 0.0) -> Dict[str, Any]:
        """
        Main analysis entry point.

        minifig_value_new/used: total market value of the set's minifigs. When
        known, it sets a price floor — a "used set" listing worth less than its
        own figures is a parted-out listing, not a complete set.
        """
        results = {}
        results["new"] = self._analyze_condition("new", minifig_value_new)
        results["used"] = self._analyze_condition("used", minifig_value_used)

        results["deep_dive"] = self._analyze_investment_potential(results["new"])
        results["part_out"] = self._analyze_part_out_potential(results["new"]["market_price"])
        results["meta"] = self.meta
        return results

    def _analyze_condition(self, condition: str, minifig_val: float = 0.0) -> Dict[str, Any]:
        sold_raw = self.data.get(condition, {}).get("sold", [])
        stock_raw = self.data.get(condition, {}).get("stock", [])

        # Layer 1: description-based completeness
        sold_data = [x for x in sold_raw if self._is_strictly_complete(x)]
        stock_data = [x for x in stock_raw if self._is_strictly_complete(x)]

        original_count = len(sold_data) + len(stock_data)

        # Layer 2: minifig floor — a used set can't be worth less than its figs
        if condition == "used" and minifig_val > 0:
            min_allowed = minifig_val * 0.80
            sold_data = [x for x in sold_data if x['price'] >= min_allowed]
            stock_data = [x for x in stock_data if x['price'] >= min_allowed]

        # Layer 3: dynamic price floor at 20% of the median (drops box-only lots)
        all_prices = [x['price'] for x in sold_data] + [x['price'] for x in stock_data]
        price_floor = 0
        if all_prices:
            if self.meta.get("year_released") == datetime.now().year:
                price_floor = 1  # brand-new sets are volatile; don't over-filter
            else:
                price_floor = statistics.median(all_prices) * 0.20

        sold_data = [x for x in sold_data if x['price'] >= price_floor]
        stock_data = [x for x in stock_data if x['price'] >= price_floor]

        sold_res = self._process_dataset(sold_data)
        stock_res = self._process_dataset(stock_data)

        sold_price = sold_res["avg"]
        stock_anchor = self._get_competitive_stock_price(stock_res["clean_items"])
        sold_count = sold_res["final_count"]

        # Layer 4: blend actual sales with the competitive asking price. With
        # few or no sales the asking anchor is the more robust signal.
        if sold_count >= 10:
            market_price = (sold_price * 0.70) + (stock_anchor * 0.30)
            confidence = "HIGH"
        elif sold_count >= 2:
            market_price = (sold_price * 0.60) + (stock_anchor * 0.40)
            confidence = "MEDIUM"
        else:
            market_price = stock_anchor
            confidence = "LOW"

        # Heavy filtering means the remaining sample is less representative.
        final_count = len(sold_res["clean_items"]) + len(stock_res["clean_items"])
        if original_count > 0:
            filtered_pct = (original_count - final_count) / original_count
            if filtered_pct > 0.30 and confidence == "HIGH":
                confidence = "MEDIUM"
                logging.info("Confidence downgraded: %.1f%% of listings filtered",
                             filtered_pct * 100)

        if market_price == 0 and sold_price > 0:
            market_price = sold_price

        return {
            "market_price": round(market_price, 2),
            "range": (round(market_price * 0.9, 2), round(market_price * 1.1, 2)),
            "buy_target": round(market_price * 0.80, 2),
            "confidence": confidence,
            "stats": {"sold": sold_res, "stock": stock_res, "stock_anchor": stock_anchor}
        }

    def _analyze_investment_potential(self, new_data: Dict) -> Dict[str, Any]:
        """Compares the cheapest clean listing against market price ("sniper")."""
        year = self.meta.get("year_released")
        curr = datetime.now().year
        status, desc = "UNKNOWN", "Year not found"

        if year:
            age = curr - year
            if age <= 1:
                status, desc = "NEW", "Flooded market"
            elif 2 <= age <= 4:
                status, desc = "EOL WATCH", "Production ending soon"
            else:
                status, desc = "RETIRED", "Production stopped"

        stock = new_data["stats"]["stock"]["clean_items"]
        mkt = new_data["market_price"]
        best = None

        if stock:
            cheapest_item = sorted(stock, key=lambda x: x['price'])[0]
            cheapest_price = cheapest_item['price']

            profit, margin = 0, 0
            if mkt > 0 and cheapest_price > 0:
                profit = mkt - (cheapest_price * 1.13)
                margin = (profit / cheapest_price) * 100

            rating = "IRRELEVANT"
            if margin >= 20:
                rating = "EXCELLENT"
            elif margin >= 10:
                rating = "GOOD"

            if status in ["EOL WATCH", "RETIRED"] and rating == "GOOD":
                rating = "GREAT INVEST"

            best = {
                "price": cheapest_price,
                "margin_pct": round(margin, 1),
                "profit_abs": round(profit, 2),
                "rating": rating
            }

        return {"lifecycle": {"status": status, "year": year, "desc": desc}, "sniper": best}

    def _get_competitive_stock_price(self, items):
        """Median of the cheapest half of live listings — what a buyer actually pays."""
        if not items:
            return 0.0
        sorted_s = sorted(items, key=lambda x: x['price'])
        cutoff = max(1, int(len(sorted_s) * 0.50))
        subset = sorted_s[:cutoff]
        if not subset:
            return 0.0
        return statistics.median([x['price'] for x in subset])

    def _weighted_avg(self, items):
        """Quantity-weighted mean. Kept for callers that want it; the central
        estimator below uses the median instead (outlier-resistant)."""
        if not items:
            return 0.0
        val = sum(x["price"] * x["qty"] for x in items)
        qty = sum(x["qty"] for x in items)
        return val / qty if qty > 0 else 0.0

    def _analyze_part_out_potential(self, mkt: float) -> Dict[str, Any]:
        specs = self.meta.get("specs", {})
        parts, w, minifigs = specs.get("parts", 0), specs.get("weight_g", 0), specs.get("minifigs", 0)
        ppp = mkt / parts if parts > 0 else 0
        ppg = mkt / w if w > 0 else 0
        rating, reason = "LOW", "Expensive per piece"
        if parts > 0:
            if ppp < 0.25:
                rating, reason = "HIGH", "Excellent PPP (<0.25)"
            elif ppp < 0.35:
                rating, reason = "MEDIUM", "Decent PPP"
        return {"ppp": round(ppp, 3), "ppg": round(ppg, 3), "parts_count": parts,
                "weight_g": w, "minifigs_count": minifigs, "rating": rating, "reason": reason}

    def _process_dataset(self, items: List[Dict]) -> Dict[str, Any]:
        """Drops bulk lots and IQR outliers, then returns the median price."""
        complete_items = [
            x for x in items
            if x.get('status') == 'complete' and self._is_strictly_complete(x)
        ]

        if not complete_items:
            return {"avg": 0.0, "final_count": 0, "clean_items": []}

        no_bulk = [x for x in complete_items if x["qty"] <= self.BULK_THRESHOLD]

        final = no_bulk
        if len(no_bulk) >= 5:
            try:
                vals = []
                for x in no_bulk:
                    vals.extend([x["price"]] * x["qty"])
                if len(vals) >= 4:
                    q1, q3 = statistics.quantiles(vals, n=4)[0], statistics.quantiles(vals, n=4)[2]
                    iqr = q3 - q1
                    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                    final = [x for x in no_bulk if low <= x["price"] <= high]
            except statistics.StatisticsError:
                pass

        avg_val = statistics.median([x['price'] for x in final]) if final else 0.0

        return {
            "avg": avg_val,
            "final_count": len(final),
            "clean_items": final
        }
