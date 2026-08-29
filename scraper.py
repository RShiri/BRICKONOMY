import time, random, re
from datetime import datetime
from typing import Dict, Any, List
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup, Tag
from database import Database

class BrickLinkScraper:
    BASE_URL = "https://www.bricklink.com/v2/catalog/catalogitem.page"
    INV_URL = "https://www.bricklink.com/catalogItemInv.asp"

    def __init__(self):
        self.db = Database()
        self.current_type = 'S'

    def _is_cache_valid(self, item_id: str) -> bool:
        """Checks if valid cache exists for item (less than 7 days old)"""
        item = self.db.get_item(item_id)
        if not item: return False
        
        # Check timestamp
        last_updated = item.get("meta", {}).get("timestamp") or item.get("meta", {}).get("cache_date")
        if not last_updated: return False
        
        try:
            # Simple check: is it from today? Or just return True if exists (User didn't specify TTL)
            # For now, let's just assume if it exists it's valid, unless forced.
            # But wait, we want fresh prices. Let's say 3 days.
            last_date = datetime.fromisoformat(last_updated.split('T')[0])
            days_diff = (datetime.now() - last_date).days
            return days_diff < 3
        except:
            return True # Fallback

    def _init_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless") 
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        return driver

    def get_minifigs_in_set(self, set_id: str, force: bool = False) -> List[Dict]:
        cached_data, timestamp = self.db.get_inventory(set_id)
        if not force and cached_data: return cached_data
        
        driver = self._init_driver()
        minifigs_dict = {}
        try:
            url = f"{self.INV_URL}?S={set_id if '-' in set_id else f'{set_id}-1'}&viewItemType=M"
            driver.get(url)
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            for row in soup.find_all('tr'):
                mf_link = row.find('a', href=re.compile(r'\?M='))
                if mf_link:
                    mf_id = mf_link.get('href').split('?M=')[1].split('&')[0]
                    if mf_id not in minifigs_dict:
                        minifigs_dict[mf_id] = {'id': mf_id, 'name': mf_link.get_text(strip=True), 'qty': 1}
            res = list(minifigs_dict.values())
            self.db.save_inventory(set_id, res)
            return res
        finally: driver.quit()

    def scrape(self, item_id: str, item_type: str = 'S', force: bool = False) -> Dict[str, Any]:
        self.current_type = item_type
        if not force:
            cached = self.db.get_item(item_id)
            if cached: return cached

        driver = self._init_driver()
        try:
            url = f"{self.BASE_URL}?{item_type}={item_id}#T=P"
            driver.get(url)
            
            # המתנה לטעינת הטבלה
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "pcipgInnerTable")))
            
            # המתנה לטעינת נתונים אמיתיים (AJAX) במקום Placeholders
            WebDriverWait(driver, 15).until(
                lambda d: "ils" in d.page_source.lower() or "~" in d.page_source
            )
            
            time.sleep(1.5) # ביטחון נוסף לטעינה מלאה של כל השורות

            data = self._parse_html(item_id, driver.page_source)
            self.db.save_item(item_id, data)
            return data
        finally:
            driver.quit()

    def _parse_html(self, item_id: str, html: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, 'html.parser')
        
        # חילוץ שם הסט והשנה (למניעת KeyError ב-Runner)
        item_name = "Unknown"
        title_tag = soup.find('h1', id='item-name-title') or soup.find('title')
        if title_tag:
            item_name = title_tag.get_text().split(":")[0].strip()
        
        year = None
        year_match = re.search(r'Year Released\D*(\d{4})', soup.get_text())
        if year_match: year = int(year_match.group(1))

        data = {
            "meta": {
                "item_id": item_id,
                "item_name": item_name,
                "year_released": year,
                "specs": self._extract_specs(soup),
                "timestamp": datetime.now().isoformat()
            },
            "new": {"sold": [], "stock": []},
            "used": {"sold": [], "stock": []}
        }

        tables = soup.find_all('table', class_='pcipgInnerTable')
        if len(tables) >= 4:
            data["new"]["sold"] = self._extract_rows(tables[0], "sold")
            data["used"]["sold"] = self._extract_rows(tables[1], "sold")
            data["new"]["stock"] = self._extract_rows(tables[2], "stock")
            data["used"]["stock"] = self._extract_rows(tables[3], "stock")
        return data

    def _extract_specs(self, soup: BeautifulSoup) -> Dict[str, Any]:
        text = soup.get_text()
        specs = {"weight_g": 0, "parts": 0, "minifigs": 0}
        
        # Weight
        w_match = re.search(r'Weight:\s*([\d.]+)\s*g', text)
        if w_match: specs["weight_g"] = float(w_match.group(1))
        
        # Parts
        p_match = re.search(r'(\d+)\s*Parts', text)
        if p_match: specs["parts"] = int(p_match.group(1))
        
        # Minifigs
        m_match = re.search(r'(\d+)\s*Minifig', text)
        if m_match: specs["minifigs"] = int(m_match.group(1))
        
        return specs

    def _extract_rows(self, table: Tag, table_type: str) -> List[Dict]:
        rows = []
        for tr in table.find_all('tr'):
            tds = tr.find_all('td')
            if len(tds) < 2: continue
            
            is_inc = False
            # 1. זיהוי לפי CSS Class (השיטה הכי אמינה ל-AJAX)
            if tr.find(class_="js-item-status-incomplete") or "(i)" in tr.get_text().lower():
                is_inc = True

            try:
                # 2. חילוץ מחיר וכמות לפי סוג טבלה
                q_idx, p_idx = (-2, -1) if table_type == "sold" else (1, 2)
                p_text = tds[p_idx].get_text(strip=True).replace(',', '')
                p = float(re.sub(r'[^\d.]', '', p_text))

                # 3. זיהוי מטבע מתוך תא המחיר (BrickLink מציג לפי הסשן)
                try:
                    from brickonomy.currency import detect_currency
                    ccy = detect_currency(p_text)
                except ImportError:
                    ccy = "ILS"

                rows.append({
                    'qty': int(re.sub(r'[^\d]', '', tds[q_idx].get_text(strip=True))),
                    'price': p,
                    'currency': ccy,
                    'status': "incomplete" if is_inc else "complete"
                })
            except: continue
        return rows