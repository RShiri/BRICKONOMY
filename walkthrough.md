# 🚶 BrickLink Pricer: Walkthrough Guide

This guide will take you through a typical workflow using the BrickLink Pricer and Sniper tool, from fetching data for a new set to analyzing it on the dashboard.

## 🏁 Scenario: Analyzing the "UCS Millennium Falcon" (75192)

You are considering buying or selling the LEGO Star Wars UCS Millennium Falcon (Set ID: `75192`). You want to know its current market price, check if there are any "sniper" deals (underpriced listings), and see if it's profitable to part it out.

### Step 1: Fetching Market Data

Open your terminal or command prompt in the project directory.

**Important:** Navigate to the `bricklink_pricer` folder if you are not already there:
```bash
cd bricklink_pricer
```

Run the following command to fetch data:

```bash
python runner.py 75192
```

**What happens next?**
1.  **Scraping**: The tool launches a browser (in standard or headless mode) and navigates to BrickLink.
2.  **Processing**: It grabs data for:
    -   Sales History (Last 6 months)
    -   Current Items for Sale
    -   Minifigures included in the set
3.  **Minifig Analysis**: It automatically detects that this set has minifigures and scrapes price data for each one individually to calculate the total minifigure value.
4.  **Reporting**: You will see a colored report in your terminal showing:
    -   Average market price (New/Used).
    -   Confidence level of the data.
    -   **Sniper Rating**: Whether there are any active listings significantly below market value.
    -   **Part Out Value**: An estimate of the set's value if broken down.

### Step 2: Batch Processing

You also want to check the "Death Star" (10188) and a specific minifigure, "Darth Vader" (sw0636).

Run them all at once:
```bash
python runner.py 75192 10188 sw0636
```

Note: The tool automatically detects that `sw0636` is a minifigure based on its ID format!

### Step 3: Visual Analysis with the Dashboard

Now that you have fetched the data, let's visualize it.

Run the dashboard:
```bash
python -m streamlit run dashboard.py
```

A new tab will open in your browser.

**Exploring the Dashboard:**
1.  **Main Table**: You will see a list of all sets you've scraped.
    -   Look for the **"Profit"** and **"Margin %"** columns. These highlight the potential profit if you were to buy the cheapest active listing and sell at market price.
    -   Click on the **"Profit"** column header to sort by the best deals.
2.  **Details View**: Click on the row for `75192`.
    -   The sidebar will update with the set's image and key stats.
    -   A **Minifigures Table** will appear below, showing the specific value of every minifigure in that set.
    -   Use the sidebar radio button to switch between "Full Database" and "Ram's Collection" (if you've configured collection tracking).

### 🧠 Understanding the Pricing Logic

The dashboard displays a **Confidence Level** (High/Medium/Low) for each price.
-   **HIGH**: Based on 10+ actual sales. High reliability.
-   **MEDIUM**: Based on 1-9 sales. Uses the sold average directly.
-   **LOW**: No sales found. Price is estimated purely from current stock listings (filtering out outliers).
-   **Price Floor**: A safety filter removes listings < 60% of the median price (except for 2025 sets) to avoid incomplete junk data.

For a full technical breakdown, check the `README.md`.


### Step 4: Maintenance

If a price seems outdated, you can force a refresh:

```bash
python runner.py 75192 --force
```

This bypasses the local cache and grabs fresh data from BrickLink.

---

### 💡 Pro Tips
-   **Green Text**: In the CLI report, green text usually indicates a good deal or high confidence.
-   **Part Out Candidate**: If "Figs % of Set" is > 80%, the tool will flag it as a "Strong Part-Out Candidate", meaning the minifigures alone might be worth more than the whole set!
