import streamlit as st
import pandas as pd
import json
import sqlite3
import os
from database import Database
from pricing_engine import PriceAnalyzer

# 1. Page Configuration
st.set_page_config(page_title="BrickLink Sniper", layout="wide")

# 2. Fixed Image Logic
def get_img_url(item_id):
    item_id = str(item_id).strip()
    is_fig = any(c.isalpha() for c in item_id)
    
    if is_fig:
        # Minifigures: MN/0/sh0584.png
        return f"https://img.bricklink.com/ItemImage/MN/0/{item_id}.png"
    else:
        # Sets: SN/0/76255-1.png (Adding -1 if missing)
        img_id = item_id if "-" in item_id else f"{item_id}-1"
        return f"https://img.bricklink.com/ItemImage/SN/0/{img_id}.png"

# 3. Database Delete Function
def delete_from_db(item_id):
    db = Database()
    try:
        # Delete from items table
        db.cursor.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
        # Delete from inventory if it exists
        db.cursor.execute("DELETE FROM inventory_lists WHERE set_id = ?", (item_id,))
        db.conn.commit()
        st.toast(f"Deleted {item_id} from Database", icon="🗑️")
    except Exception as e:
        st.error(f"Delete failed: {e}")
    finally:
        db.close()

# 4. Data Loading & Cleaning
@st.cache_data(show_spinner=False, ttl=10)
def load_data():
    db = Database()
    db.cursor.execute("SELECT item_id, json_data, updated_at FROM items")
    all_rows = db.cursor.fetchall()
    db.close()
    
    # Fetch Collection Info from CSV
    collection_ids = set()
    try:
        if os.path.exists("BrickEconomy-Sets(2).csv"):
            df_csv = pd.read_csv("BrickEconomy-Sets(2).csv")
            # CSV format: 76210-1 -> We need to handle this. 
            # The tool stores IDs as "76210" for sets usually, but let's check.
            # If dashboard uses "76210" and CSV has "76210-1", we need to strip "-1".
            for _, row in df_csv.iterrows():
                raw_id = str(row['Number']).strip().lower() # e.g. "76210-1" or "sh0229"
                base_id = raw_id.split('-')[0] if '-' in raw_id else raw_id
                collection_ids.add(base_id)
                collection_ids.add(raw_id) # Add both just in case
    except Exception as e:

        st.error(f"Error loading collection CSV: {e}")

    # Fetch Minifigs belonging to these sets
    inventory_map = {}
    try:
        # We need to query for every set in collection_ids
        # To be safe against SQL injection (though ids come from file), lets use placeholders
        # But list might be huge. Generating a long IN clause is okay for SQLite usually.
        # Alternatively, fetch ALL inventory and filter in python.
        db = Database()
        db.cursor.execute("SELECT set_id, json_data FROM inventory_lists")
        inv_rows = db.cursor.fetchall()
        db.close()
        
        for r in inv_rows:
            s_id = str(r[0])
            figs_data = json.loads(r[1])
            # Store for Polybag Map
            inventory_map[s_id] = [f['id'] for f in figs_data]
            
            if s_id in collection_ids or f"{s_id}-1" in collection_ids:
                # This set is in collection, add its minifigs
                for f in figs_data:
                    collection_ids.add(f['id'].lower())
    except Exception as e:
        st.error(f"Error expanding collection with minifigs: {e}")

    sets, figs = [], []
    price_map = {} # Store prices for polybag calculation: {id: price}
    
    # Stricter filter for junk entries
    junk_keywords = ['python', 'streamlit', 'n', 'test', 'runner', 'cmd']

    for row in all_rows:
        item_id = str(row[0]).strip()
        
        # Filter out junk
        if any(key in item_id.lower() for key in junk_keywords) or len(item_id) < 2:
            continue

        try:
            raw_data = json.loads(row[1])
            analysis = PriceAnalyzer(raw_data).analyze()
            sniper = analysis.get("deep_dive", {}).get("sniper", {})
            
            # Formatting Year
            yr = analysis.get("meta", {}).get("year_released")
            year_val = str(int(float(yr))) if yr and str(yr).replace('.0','').isdigit() else ""

            # Store in Price Map
            used_price = analysis.get("used", {}).get("market_price", 0)
            price_map[item_id] = used_price

            item = {
                "ID": item_id,
                "Image": get_img_url(item_id),
                "Name": analysis.get("meta", {}).get("item_name", "Unknown"),
                "Year": year_val,
                "New Price": analysis.get("new", {}).get("market_price", 0),
                "New Conf": analysis.get("new", {}).get("confidence", "N/A"),
                "Used Price": used_price,
                "Used Conf": analysis.get("used", {}).get("confidence", "N/A"),
                "Profit": sniper.get("profit_abs", 0),
                "Margin %": sniper.get("margin_pct", 0),
                "Rating": sniper.get("rating", "N/A"),
                "InCollection": item_id.lower() in collection_ids
            }
            
            if any(c.isalpha() for c in item_id): figs.append(item)
            else: sets.append(item)
        except: continue
    
    # --- Polybag Override Logic ---
    # If item is a polybag/foil pack, override Used Price with Sum of Minifigs
    for s in sets:
        name_lower = s["Name"].lower()
        if "polybag" in name_lower or "foil pack" in name_lower:
            # Check if we have minifigs for this set
            s_id = s["ID"]
            # Handle ID mismatch (76210 vs 76210-1)
            # Inventory map usually stores just the number part if scraped that way, 
            # but let's check exact match first.
            fig_ids = inventory_map.get(s_id)
            if not fig_ids and "-" in s_id:
                 fig_ids = inventory_map.get(s_id.split("-")[0]) # Try without suffix
            
            if fig_ids:
                fig_sum = sum(price_map.get(fid, 0) for fid in fig_ids)
                if fig_sum > 0:
                    s["Used Price"] = fig_sum
                    s["Used Conf"] = "Polybag (Figs)"

    return pd.DataFrame(sets), pd.DataFrame(figs)

st.title("🧱 BrickLink Sniper Dashboard")

# --- FILTER BAR ---
filter_mode = st.sidebar.radio("Show:", ["Ram's Collection", "Full Database"], index=0)

df_sets, df_figs = load_data()

if filter_mode == "Ram's Collection":
    if not df_sets.empty: df_sets = df_sets[df_sets["InCollection"]]
    if not df_figs.empty: df_figs = df_figs[df_figs["InCollection"]]

# --- MAIN VIEW ---
st.subheader("📦 Sets")
if not df_sets.empty:
    df_sets = df_sets.sort_values("Profit", ascending=False)
    
    # Calculate Totals
    total_new = df_sets["New Price"].sum()
    total_used = df_sets["Used Price"].sum()
    total_profit = df_sets["Profit"].sum()
    avg_margin = df_sets["Margin %"].mean()
    
    # Display Totals
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total New Value", f"{total_new:,.2f} ₪")
    c2.metric("Total Used Value", f"{total_used:,.2f} ₪")
    c3.metric("Total Potential Profit", f"{total_profit:,.2f} ₪")
    c4.metric("Avg Margin", f"{avg_margin:.1f}%")
    
    # Selection Event
    set_selection = st.dataframe(
        df_sets,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Image": st.column_config.ImageColumn("Preview"),
            "New Price": st.column_config.NumberColumn(format="%.2f ₪"),
            "New Conf": st.column_config.TextColumn("Confidence"),
            "Used Price": st.column_config.NumberColumn(format="%.2f ₪"),
            "Used Conf": st.column_config.TextColumn("Confidence"),
            "Profit": st.column_config.NumberColumn(format="%.2f ₪"),
            "Margin %": st.column_config.NumberColumn(format="%.1f%%"),
        }
    )
else:
    st.info("No sets found in database.")

st.divider()

# --- SIDEBAR: DELETE & DETAILS ---
st.sidebar.header("Item Actions")
selected_id = None

# If a row is clicked in the main table
if 'set_selection' in locals() and len(set_selection.selection.rows) > 0:
    row_idx = set_selection.selection.rows[0]
    selected_item = df_sets.iloc[row_idx]
    selected_id = selected_item["ID"]
    
    st.sidebar.image(selected_item["Image"], caption=selected_item["Name"])
    st.sidebar.markdown(f"**ID:** `{selected_id}`")
    st.sidebar.markdown(f"**Profit:** {selected_item['Profit']:.2f} ₪")
    
    if st.sidebar.button("🗑️ Delete from DB", type="primary", use_container_width=True):
        delete_from_db(selected_id)
        st.cache_data.clear() # Force clear cache to update UI
        st.rerun()

# --- DYNAMIC MINIFIGS TABLE ---
if selected_id:
    st.subheader(f"👥 Minifigures in {selected_id}")
    db = Database()
    db.cursor.execute("SELECT json_data FROM inventory_lists WHERE set_id = ?", (selected_id,))
    inv = db.cursor.fetchone()
    db.close()
    
    if inv:
        ids_in_set = [m['id'] for m in json.loads(inv[0])]
        relevant_figs = df_figs[df_figs["ID"].isin(ids_in_set)]
        if not relevant_figs.empty:
            st.dataframe(relevant_figs, use_container_width=True, hide_index=True,
                         column_config={"Image": st.column_config.ImageColumn("Preview"), 
                                        "New Price": st.column_config.NumberColumn(format="%.2f ₪"),
                                        "New Conf": st.column_config.TextColumn("Conf"),
                                        "Used Price": st.column_config.NumberColumn(format="%.2f ₪"),
                                        "Used Conf": st.column_config.TextColumn("Conf")})
        else:
            st.info("Minifigure data not found. Scrape this set again.")
    else:
        st.warning("No inventory found for this set.")
else:
    st.subheader("👤 All Tracked Minifigures")
    if not df_figs.empty:
        # Calculate Totals
        total_fig_new = df_figs["New Price"].sum()
        total_fig_used = df_figs["Used Price"].sum()
        
        c1, c2 = st.columns(2)
        c1.metric("Total Minifigure Value (New)", f"{total_fig_new:,.2f} ₪")
        c2.metric("Total Minifigure Value (Used)", f"{total_fig_used:,.2f} ₪")
        
        st.dataframe(df_figs, use_container_width=True, hide_index=True,
                     column_config={"Image": st.column_config.ImageColumn("Preview"),
                                    "New Price": st.column_config.NumberColumn(format="%.2f ₪"),
                                    "New Conf": st.column_config.TextColumn("Conf"),
                                    "Used Price": st.column_config.NumberColumn(format="%.2f ₪"),
                                    "Used Conf": st.column_config.TextColumn("Conf")})