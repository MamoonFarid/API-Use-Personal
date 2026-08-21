# ----------------------------------------------------
# TAB 2: LIVE KEEPA DATABASE DISCOVERY (FIXED)
# ----------------------------------------------------
with tab2:
    st.subheader("Filter Amazon UK Database for Winning Products")
    
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        category_map = {
            "Pet Supplies": 340840031,
            "Home & Kitchen": 11052681,
            "Beauty": 66268031,
            "Health & Personal Care": 65801031,
            "Toys & Games": 468292,
            "DIY & Tools": 79903031,
            "All Categories (Unrestricted)": None
        }
        selected_cat = st.selectbox("Root Category", list(category_map.keys()))
        
    with col_b:
        min_price = st.number_input("Min Buy Box Price (£)", value=18.0, step=1.0)
        max_price = st.number_input("Max Buy Box Price (£)", value=60.0, step=1.0)
        
    with col_c:
        max_bsr = st.number_input("Max Sales Rank (BSR)", value=50000, step=5000)
        min_sellers = st.slider("Min FBA Sellers", min_value=1, max_value=5, value=2)

    if st.button("🔍 Search Keepa Database"):
        # Construct Product Finder Payload
        query_payload = {
            "current_SALES_gte": 1,
            "current_SALES_lte": int(max_bsr),
            "current_BUY_BOX_SHIPPING_gte": int(min_price * 100),
            "current_BUY_BOX_SHIPPING_lte": int(max_price * 100),
            "current_COUNT_LIVE_OFFERS_SHIPPING_gte": int(min_sellers),
            "current_COUNT_LIVE_OFFERS_SHIPPING_lte": 12,
            "sort": [["current_SALES", "asc"]],
            "perPage": 40
        }
        
        # Include category only if a specific one is picked
        if category_map[selected_cat]:
            query_payload["rootCategory"] = category_map[selected_cat]

        with st.spinner("Searching Amazon UK database..."):
            try:
                # Use native keepa Product Finder via POST
                url = f"https://api.keepa.com/query?key={api_key}&domain={DOMAIN_UK}"
                resp = requests.post(url, data=json.dumps(query_payload), headers={"Content-Type": "application/json"})
                
                if resp.status_code == 200:
                    asins = resp.json().get("asinList", [])
                    st.success(f"Matched **{len(asins)}** high-demand ASINs!")
                    
                    if asins:
                        # Batch-fetch full product data
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
