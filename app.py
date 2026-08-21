import streamlit as st
import pandas as pd
import requests
import json
import keepa

st.set_page_config(layout="wide", page_title="Amazon UK Wholesale Hub")
st.title("🇬🇧 Amazon UK Wholesale Sourcing & Analytics Hub")

# Sidebar Configuration
st.sidebar.header("🔑 API Settings")
api_key = st.sidebar.text_input("Enter Keepa API Key", type="password")

if not api_key:
    st.info("👈 Please enter your Keepa API Key in the sidebar to get started.")
    st.stop()

# Initialize Keepa Client
api = keepa.Keepa(api_key)
DOMAIN_UK = 2

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
            
            with st.spinner(f"Scanning {len(eans)} products against Amazon UK..."):
                try:
                    products = api.query(eans, domain=DOMAIN_UK, stats=180, history=False)
                    results = []
                    for p in products:
                        if not p or 'stats' not in p or not p['stats']:
                            continue
                        
                        stats = p['stats']
                        ean = p.get('eanList', [''])[0]
                        match = df[df[ean_col].astype(str) == str(ean)]
                        cost = float(match[cost_col].values[0]) if not match.empty else 0.0
                        
                        buy_box = (stats['current'][18] / 100.0) if stats['current'][18] > 0 else 0.0
                        fba_sellers = stats['current'][10]
                        bsr = stats['current'][3]
                        amz_stock_pct = stats['inStockPercent90'][0]
                        
                        referral_fee = buy_box * 0.153
                        fba_fee = 2.85
                        inbound_ship = 0.35
                        
                        net_profit = buy_box - (cost + referral_fee + fba_fee + inbound_ship)
                        net_roi = (net_profit / cost * 100) if cost > 0 else 0
                        
                        results.append({
                            "ASIN": p.get('asin', ''),
                            "Title": p.get('title', '')[:50] + "...",
                            "Cost (£)": round(cost, 2),
                            "Buy Box (£)": round(buy_box, 2),
                            "Net Profit (£)": round(net_profit, 2),
                            "Net ROI (%)": round(net_roi, 1),
                            "BSR": bsr,
                            "FBA Sellers": fba_sellers,
                            "Amazon In-Stock %": amz_stock_pct,
                            "Link": f"https://www.amazon.co.uk/dp/{p.get('asin', '')}"
                        })
                    
                    res_df = pd.DataFrame(results)
                    filtered_df = res_df[
                        (res_df['Amazon In-Stock %'] < 5) & 
                        (res_df['FBA Sellers'].between(3, 8)) & 
                        (res_df['Buy Box (£)'] >= 18.0) &
                        (res_df['Net ROI (%)'] >= 20.0)
                    ]
                    st.success(f"Found {len(filtered_df)} winning opportunities!")
                    st.dataframe(filtered_df, use_container_width=True)
                except Exception as e:
                    st.error(f"Error processing CSV: {e}")

# ----------------------------------------------------
# TAB 2: LIVE KEEPA DATABASE DISCOVERY (FIXED)
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
        # Keepa Product Finder JSON payload
        selection = {
            "current_SALES_gte": 1,
            "current_SALES_lte": int(max_bsr),
            "current_BUY_BOX_SHIPPING_gte": int(min_price * 100),
            "current_BUY_BOX_SHIPPING_lte": int(max_price * 100),
            "current_COUNT_LIVE_OFFERS_SHIPPING_gte": int(min_sellers),
            "current_COUNT_LIVE_OFFERS_SHIPPING_lte": 12,
            "sort": [["current_SALES", "asc"]],
            "perPage": 50,  # Valid range: 50 to 10000
            "page": 0       # 0-indexed page
        }
        
        if category_map[selected_cat]:
            selection["rootCategory"] = category_map[selected_cat]

        with st.spinner("Searching Amazon UK database..."):
            try:
                # Query Keepa /query endpoint with selection payload
                url = f"https://api.keepa.com/query?key={api_key}&domain={DOMAIN_UK}&selection={json.dumps(selection)}"
                resp = requests.get(url)
                
                if resp.status_code == 200:
                    asins = resp.json().get("asinList", [])
                    st.success(f"Matched **{len(asins)}** high-demand ASINs!")
                    
                    if asins:
                        # Batch-fetch full product metrics for the matched ASINs
                        products = api.query(asins[:30], domain=DOMAIN_UK, stats=180, history=False)
                        shortlist = []
                        for p in products:
                            if not p or 'stats' not in p or not p['stats']:
                                continue
                            stats = p['stats']
                            current_bb = (stats['current'][18] / 100.0) if stats['current'][18] > 0 else 0.0
                            avg90_bb = (stats['avg90'][18] / 100.0) if stats['avg90'][18] > 0 else current_bb
                            
                            shortlist.append({
                                "ASIN": p.get('asin', ''),
                                "Brand": p.get('brand', 'Unknown'),
                                "Title": p.get('title', '')[:50] + "...",
                                "Buy Box (£)": round(current_bb, 2),
                                "90-Day Avg (£)": round(avg90_bb, 2),
                                "BSR": stats['current'][3],
                                "FBA Sellers": stats['current'][10],
                                "Amazon Link": f"https://www.amazon.co.uk/dp/{p.get('asin', '')}"
                            })
                        
                        st.dataframe(pd.DataFrame(shortlist), use_container_width=True)
                else:
                    st.error(f"Keepa API Error ({resp.status_code}): {resp.text}")
            except Exception as e:
                st.error(f"Failed to query Keepa: {e}")
