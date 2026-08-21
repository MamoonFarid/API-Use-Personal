import streamlit as st
import pandas as pd
import requests
import json

st.set_page_config(layout="wide", page_title="Amazon UK Wholesale Hub")
st.title("🇬🇧 Amazon UK Wholesale Sourcing & Analytics Hub")

# Sidebar Configuration
st.sidebar.header("🔑 API Settings")
api_key = st.sidebar.text_input("Enter Keepa API Key", type="password")

if not api_key:
    st.info("👈 Please enter your Keepa API Key in the sidebar to get started.")
    st.stop()

DOMAIN_UK = 2

# Helper Function: Safely extract product data from Keepa JSON
def extract_keepa_metrics(p):
    stats = p.get('stats', {})
    if not stats:
        return None
    
    current = stats.get('current', [])
    avg90 = stats.get('avg90', [])
    
    # Keepa indices: 18 = Buy Box, 3 = Sales Rank (BSR), 10 = Count New FBA Offers
    buy_box = (current[18] / 100.0) if len(current) > 18 and current[18] > 0 else 0.0
    avg90_bb = (avg90[18] / 100.0) if len(avg90) > 18 and avg90[18] > 0 else buy_box
    bsr = current[3] if len(current) > 3 and current[3] > 0 else 0
    fba_sellers = current[10] if len(current) > 10 and current[10] >= 0 else 0
    
    in_stock_90 = stats.get('inStockPercent90', [])
    amz_stock_pct = in_stock_90[0] if len(in_stock_90) > 0 else 0
    
    return {
        "asin": p.get('asin', ''),
        "brand": p.get('brand', 'Unknown'),
        "title": p.get('title', 'Unknown Title'),
        "buy_box": buy_box,
        "avg90_bb": avg90_bb,
        "bsr": bsr,
        "fba_sellers": fba_sellers,
        "amz_stock_pct": amz_stock_pct,
        "ean_list": p.get('eanList', [])
    }

# Tab Layout
tab1, tab2 = st.tabs(["📁 Scan Wholesale CSV", "🔍 Keepa Database Discovery"])

# ----------------------------------------------------
# TAB 1: WHOLESALE CSV SCANNER
# ----------------------------------------------------
with tab1:
    st.subheader("Upload & Scan Wholesaler Catalog")
    uploaded_file = st.file_uploader("Upload Wholesaler Catalog (CSV or Excel)", type=["csv", "xlsx"])

    if uploaded_file:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith(".csv") else pd.read_excel(uploaded_file)
        st.write(f"Loaded **{len(df)}** rows from supplier sheet.")
        st.dataframe(df.head(3), use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            ean_col = st.selectbox("Select EAN/Barcode Column", df.columns)
        with col2:
            cost_col = st.selectbox("Select Cost Price Column", df.columns)

        if st.button("🚀 Run CSV Analysis"):
            eans = df[ean_col].dropna().astype(str).tolist()
            # Batch in chunks of 50 to avoid URL limits
            batch_eans = ",".join(eans[:50])
            
            with st.spinner("Scanning products against Amazon UK..."):
                try:
                    url = f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}&code={batch_eans}&stats=180&history=0"
                    resp = requests.get(url)
                    
                    if resp.status_code == 200:
                        products = resp.json().get('products', [])
                        results = []
                        for p in products:
                            m = extract_keepa_metrics(p)
                            if not m:
                                continue
                            
                            ean = m['ean_list'][0] if m['ean_list'] else ''
                            match = df[df[ean_col].astype(str) == str(ean)]
                            cost = float(match[cost_col].values[0]) if not match.empty else 0.0
                            
                            referral_fee = m['buy_box'] * 0.153
                            fba_fee = 2.85
                            inbound_ship = 0.35
                            
                            net_profit = m['buy_box'] - (cost + referral_fee + fba_fee + inbound_ship)
                            net_roi = (net_profit / cost * 100) if cost > 0 else 0
                            
                            results.append({
                                "ASIN": m['asin'],
                                "Title": m['title'][:45] + "...",
                                "Cost (£)": round(cost, 2),
                                "Buy Box (£)": round(m['buy_box'], 2),
                                "Net Profit (£)": round(net_profit, 2),
                                "Net ROI (%)": round(net_roi, 1),
                                "BSR": m['bsr'],
                                "FBA Sellers": m['fba_sellers'],
                                "Amazon In-Stock %": m['amz_stock_pct'],
                                "Link": f"https://www.amazon.co.uk/dp/{m['asin']}"
                            })
                        
                        res_df = pd.DataFrame(results)
                        if not res_df.empty:
                            filtered_df = res_df[
                                (res_df['Amazon In-Stock %'] < 5) & 
                                (res_df['FBA Sellers'].between(2, 10)) & 
                                (res_df['Buy Box (£)'] >= 18.0) &
                                (res_df['Net ROI (%)'] >= 20.0)
                            ]
                            st.success(f"Found {len(filtered_df)} winning opportunities!")
                            st.dataframe(filtered_df, use_container_width=True)
                        else:
                            st.warning("No valid matching products found.")
                    else:
                        st.error(f"Keepa API Error ({resp.status_code}): {resp.text}")
                except Exception as e:
                    st.error(f"Error processing CSV: {e}")

# ----------------------------------------------------
# TAB 2: LIVE KEEPA DATABASE DISCOVERY
# ----------------------------------------------------
with tab2:
    st.subheader("Filter Amazon UK Database for Winning Products")
    
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        category_map = {
            "All Categories (Unrestricted)": None,
            "Pet Supplies": 340840031,
            "Home & Kitchen": 11052681,
            "Beauty": 66268031,
            "Health & Personal Care": 65801031,
            "Toys & Games": 468292,
            "DIY & Tools": 79903031
        }
        selected_cat = st.selectbox("Root Category", list(category_map.keys()))
        
    with col_b:
        min_price = st.number_input("Min Buy Box Price (£)", value=18.0, step=1.0)
        max_price = st.number_input("Max Buy Box Price (£)", value=60.0, step=1.0)
        
    with col_c:
        max_bsr = st.number_input("Max Sales Rank (BSR)", value=50000, step=5000)
        min_sellers = st.slider("Min FBA Sellers", min_value=1, max_value=5, value=2)

    if st.button("🔍 Search Keepa Database"):
        selection = {
            "current_SALES_gte": 1,
            "current_SALES_lte": int(max_bsr),
            "current_BUY_BOX_SHIPPING_gte": int(min_price * 100),
            "current_BUY_BOX_SHIPPING_lte": int(max_price * 100),
            "current_COUNT_LIVE_OFFERS_SHIPPING_gte": int(min_sellers),
            "current_COUNT_LIVE_OFFERS_SHIPPING_lte": 12,
            "sort": [["current_SALES", "asc"]],
            "perPage": 50,
            "page": 0
        }
        
        if category_map[selected_cat]:
            selection["rootCategory"] = category_map[selected_cat]

        with st.spinner("Querying Keepa Database..."):
            try:
                # 1. Get ASIN list
                query_url = f"https://api.keepa.com/query?key={api_key}&domain={DOMAIN_UK}&selection={json.dumps(selection)}"
                query_resp = requests.get(query_url)
                
                if query_resp.status_code == 200:
                    asins = query_resp.json().get("asinList", [])
                    st.success(f"Matched **{len(asins)}** high-demand ASINs!")
                    
                    if asins:
                        # 2. Batch fetch product metrics using raw REST API
                        asin_csv = ",".join(asins[:30])
                        prod_url = f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}&asin={asin_csv}&stats=180&history=0"
                        prod_resp = requests.get(prod_url)
                        
                        if prod_resp.status_code == 200:
                            products = prod_resp.json().get('products', [])
                            shortlist = []
                            for p in products:
                                m = extract_keepa_metrics(p)
                                if not m:
                                    continue
                                shortlist.append({
                                    "ASIN": m['asin'],
                                    "Brand": m['brand'],
                                    "Title": m['title'][:50] + "...",
                                    "Buy Box (£)": round(m['buy_box'], 2),
                                    "90-Day Avg (£)": round(m['avg90_bb'], 2),
                                    "BSR": m['bsr'],
                                    "FBA Sellers": m['fba_sellers'],
                                    "Amazon Link": f"https://www.amazon.co.uk/dp/{m['asin']}"
                                })
                            
                            st.dataframe(pd.DataFrame(shortlist), use_container_width=True)
                        else:
                            st.error(f"Keepa Product API Error: {prod_resp.text}")
                else:
                    st.error(f"Keepa Query API Error ({query_resp.status_code}): {query_resp.text}")
            except Exception as e:
                st.error(f"Execution Error: {e}")
