# 🧱 BrickLink Pricer & Sniper

A powerful Python toolkit for scraping BrickLink market data, analyzing trends, identifying "Sniper" deals, and calculating Part Out Values (POV). Includes a CLI for data gathering and a Streamlit dashboard for visual analytics.

## 🌐 Brickonomy — the web app

`brickonomy/` is a self-hosted, BrickEconomy-style site built on top of this
scraper: every LEGO set ever released, real price history from BrickLink +
eBay + BrickOwl, growth & forecast charts, minifig pages, part-out values,
deals and portfolio tracking.

**Run it on your PC:** double-click **`run.bat`** (Windows) or `./run.sh`
(macOS/Linux). It sets everything up by itself and opens the browser.
Never used Python? → **[QUICKSTART.md](QUICKSTART.md)**.
Prices are collected by **`scan.bat`** / `./scan.sh`.
Technical details → [`brickonomy/README.md`](brickonomy/README.md).

## ✨ Features

### 🔍 CLI Scraper (`runner.py`)
- **Market Pricing**: Scrapes "New" and "Used" market prices for Sets and Minifigures.
- **Trend Analysis**: Tracks price changes over time (▲/▼) and displays percentage shifts.
- **Deal Finding ("Sniper")**: Identifies underpriced listings based on calculated market value.
- **Part Out Value (POV)**: Estimates profitable breakdown values for sets.
- **Minifigure Breakdown**: Automatically fetches and values all minifigures within a set.
- **Data Integrity**: Intelligent outlier filtering and integrity checks (listings vs sales).
- **Caching**: Local caching to minimize repeated scrapes.

### 📊 Dashboard (`dashboard.py`)
- **Interactive Analytics**: Sortable tables for Sets and Minifigures.
- **Profit Tracking**: Visualizes potential profit margins and "Sniper" ratings.
- **Collection Management**: Filter between "Ram's Collection" and the full database.
- **Drill-Down Details**: Inspect individual set components and minifigures.
- **Visuals**: Displays set/minifigure images directly in the UI.

## 🚀 Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/bricklink-pricer.git
    cd bricklink-pricer
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    *Dependencies include: `selenium`, `beautifulsoup4`, `streamlit`, `pandas`, `colorama`, `tqdm`.*

3.  **Driver Setup**:
    - Ensure you have a compatible WebDriver (e.g., ChromeDriver) installed if Selenium requires it, although modern Selenium often manages this automatically.

## 🛠️ Usage

### 1. The CLI Runner (`runner.py`)
Use this to fetch data.

**Basic Usage:**
```bash
python runner.py 75192 10188
```
*Fetches data for sets 75192 and 10188.*

**Force Refresh:**
```bash
python runner.py 75192 --force
```
*Ignores cache and scrapes fresh data.*

**Specific Type:**
```bash
python runner.py sw0001 --type M
```
*Explicitly treats the ID as a Minifigure (`M`). The tool usually auto-detects this.*

### 2. The Dashboard (`dashboard.py`)
Use this to visualize the data.

```bash
streamlit run dashboard.py
```
*Opens the dashboard in your default web browser (usually http://localhost:8501).*

## 📂 Project Structure

- `runner.py`: Main CLI entry point.
- `scraper.py`: Selenium/BS4 scraping logic.
- `pricing_engine.py`: Data analysis and pricing algorithms.
- `dashboard.py`: Streamlit frontend.
- `database.py`: SQLite database handler (`bricklink_data.db`).
- `data/`: Backup/migration data.
- `cache/`: Temporary cache files.

## 🤝 Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## 🧠 Pricing Engine Logic

The tool uses a multi-step algorithm to determine the "Market Value" of a set.

### 1. Data Cleaning
-   **Blacklist Filter**: Removes listings with words like "incomplete", "no minifigs", "box only".
-   **Price Floor**:
    -   Calculates the global median price for the item.
    -   Removes any listing that is **< 60%** of the median (filters out scams or partial sets).
    -   *Exception*: Sets released in **2025** have a floor of 1 ILS to allow early market data.

### 2. Market Price Algorithm
The price is determined based on the **volume of sales**:

| Sales Count (Sold Items) | Calculation Logic | Confidence Level |
| :--- | :--- | :--- |
| **High Volume (10+)** | `(70% × Sold Avg) + (30% × Stock Price)` | **HIGH** |
| **Low Volume (1-9)** | `100% × Sold Avg` (Stock ignored) | **MEDIUM** |
| **No Sales (0)** | `100% × Stock Price` | **LOW** |

> **Note**: "Stock Price" is not a simple average. It uses a "Competitive Anchor" which averages only the **cheapest 35%** of listings to reflect realistic sellable prices rather than overpriced wishlist items.


## 📄 License
[MIT](https://choosealicense.com/licenses/mit/)
