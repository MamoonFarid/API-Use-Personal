import streamlit as st
import pandas as pd
import keepa

st.set_page_config(layout="wide", page_title="Amazon UK Wholesale Scanner")

st.title("🇬🇧 Amazon UK Wholesale Catalog Scanner")

# Secure API Key Input via Streamlit Secrets or Sidebar
api_key = st.sidebar.text_input("Enter Keepa API Key", type="password")

if not api_key:
    st.info("👈 Please enter your Keepa API Key in the sidebar to begin.")
    st.stop()

api = keepa.Keepa(api_key)

uploaded_file = st.file_uploader("Upload Wholesaler Catalog (CSV / Excel)", type=["csv", "xlsx"])

if uploaded_file:
    # 1. Parse File
    if uploaded_file.name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
    
    st.write(f"Loaded **{len(df)}** rows from supplier sheet.")
    st.dataframe(df.head(3), use_container_width=True)

    # 2. Select EAN & Cost Price Columns
    col1, col2 = st.columns(2)
    with col1:
        ean_col = st.selectbox("Select EAN/Barcode Column", df.columns)
    with col2:
        cost_col = st.selectbox("Select Cost Price Column", df.columns)

    if st.button("🚀 Run Analysis on Amazon UK"):
        eans = df[ean_col].dropna().astype(str).tolist()
        
        with st.spinner(f"Querying Keepa API for {len(eans)} products..."):
            try:
                # domain 2 = amazon.co.uk
                products = api.query(eans, domain=2, stats=180, history=False)
                
                results = []
                for p in products:
                    if not p or 'stats' not in p or not p['stats']:
                        continue
                    
                    stats = p['stats']
                    ean = p.get('eanList', [''])[0]
                    
                    # Match Cost Price from file
                    match = df[df[ean_col].astype(str) == str(ean)]
                    cost = float(match[cost_col].values[0]) if not match.empty else 0.0
                    
                    # Prices in Keepa are in pence/cents (divide by 100)
                    buy_box = (stats['current'][18] / 100.0) if stats['current'][18] > 0 else 0.0
                    avg90_bb = (stats['avg90'][18] / 100.0) if stats['avg90'][18] > 0 else buy_box
                    fba_sellers = stats['current'][10]
                    bsr = stats['current'][3]
                    amz_stock_pct = stats['inStockPercent90'][0]
                    
                    # Standard Amazon UK Wholesale Cost Structure
                    referral_fee = buy_box * 0.153
                    fba_fee = 2.85  # Standard UK parcel estimate
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
                
                # Apply Winning Filter Matrix
                st.subheader("🎯 Qualified Wholesale Opportunities")
                filtered_df = res_df[
                    (res_df['Amazon In-Stock %'] < 5) & 
                    (res_df['FBA Sellers'].between(3, 8)) & 
                    (res_df['Buy Box (£)'] >= 18.0) &
                    (res_df['Net ROI (%)'] >= 20.0)
                ]
                
                st.dataframe(filtered_df, use_container_width=True)
                
            except Exception as e:
                st.error(f"Error querying Keepa API: {e}")
