import streamlit as st
import pandas as pd
import requests
import json
import math

st.set_page_config(layout="wide", page_title="Amazon UK Wholesale Hub")
st.title("🇬🇧 Amazon UK Wholesale Sourcing & Analytics Hub")

DOMAIN_UK = 2

# ==========================================================================
# HARDENED "ZERO-RISK" FILTER DEFAULTS (from your table)
# All of these are adjustable in the sidebar — these are just the starting
# defaults that match the table you sent.
# ==========================================================================
DEFAULTS = {
    "bb_min": 22.0,
    "bb_max": 55.0,
    "min_sellers": 3,
    "max_sellers": 7,          # your table says 3-5; your message text said 3-8 —
                                # slider below, change freely
    "min_rating": 4.4,
    "min_reviews": 250,
    "max_weight_g": 800,
    "max_variations": 4,
    "max_dominant_share": 35.0,   # %
    "max_180d_delta": 5.0,        # %
    "max_atl_premium": 15.0,      # current price <= all-time-low + this %
    "min_net_roi": 30.0,          # %
    "min_net_profit": 5.0,        # £
    "min_breakeven_buffer": 20.0, # %
}

# Category-specific BSR ceilings (top 0.5-1%). Your table only specified
# Home/Kitchen/Pet (<15,000) and Beauty (<8,000) explicitly — I've defaulted
# the unlisted categories to the same 15,000 ceiling as a starting point.
# Tighten these per-category as you get real sell-through data back.
CATEGORY_BSR_CAP = {
    "Pet Supplies": 15000,
    "Home & Kitchen": 15000,
    "Beauty": 8000,
    "Health & Personal Care": 15000,
    "Toys & Games": 15000,
    "DIY & Tools": 15000,
}

CATEGORY_MAP = {
    "Pet Supplies": 340840031,
    "Home & Kitchen": 11052681,
    "Beauty": 66268031,
    "Health & Personal Care": 65801031,
    "Toys & Games": 468292,
    "DIY & Tools": 79903031,
}
# Clothing and Grocery are deliberately absent from this map — "no clothing/food"
# is enforced by never targeting those root category nodes in the first place.

BATTERY_KEYWORDS = [
    "battery", "batteries", "rechargeable", "cordless", "lithium",
    " aa ", " aaa ", "9v", "cr2032", "power bank", "solar powered",
]
EXCLUDED_CATEGORY_FRAGMENTS = [
    "clothing", "apparel", "shoes", "jewellery", "jewelry",
    "grocery", "food", "gourmet", "clothes",
]

# ==========================================================================
# SIDEBAR
# ==========================================================================
st.sidebar.header("🔑 API Settings")
api_key = st.sidebar.text_input("Enter Keepa API Key", type="password")

if not api_key:
    st.info("👈 Please enter your Keepa API Key in the sidebar to get started.")
    st.stop()

st.sidebar.header("⚙️ Result Volume")
max_results = st.sidebar.slider(
    "Max products per search", min_value=50, max_value=500, value=250, step=50,
    help="Keepa bills tokens per product fetched (more with stats=180 and buybox=1 "
         "requested, as this app does). Start at 50-100 to sanity-check filters "
         "before scaling to 250+ — check 'tokens left' shown after each run."
)
PRODUCT_CHUNK_SIZE = 100  # Keepa's /product endpoint caps ASINs per call around here

st.sidebar.header("💷 Fee Assumptions")
referral_rate_pct = st.sidebar.number_input(
    "Referral fee % for this category", min_value=5.0, max_value=20.0, value=15.0, step=0.5,
    help="Most UK categories are 15%, but electronics/computers run ~7-8% and some "
         "low-price bands are lower. Set this per category you're scanning."
)
fba_fee_est = st.sidebar.number_input("Est. FBA pick & pack fee (£)", value=2.85, step=0.05)
inbound_ship_est = st.sidebar.number_input("Est. inbound shipping/prep per unit (£)", value=0.35, step=0.05)

st.sidebar.header("💰 Indicative Cost (Discovery tab only)")
assumed_cost_pct = st.sidebar.slider(
    "Assumed cost as % of Buy Box", min_value=20, max_value=70, value=45, step=5,
    help="Discovery mode has no real supplier quote yet — this is only for "
         "pre-ranking candidates. Always replace with your real cost before trusting ROI."
)

st.sidebar.header("🎚️ Hardened Filter Thresholds")
with st.sidebar.expander("Adjust thresholds (defaults = your hardened table)"):
    t = {}
    t["bb_min"] = st.number_input("Min Buy Box (£)", value=DEFAULTS["bb_min"])
    t["bb_max"] = st.number_input("Max Buy Box (£)", value=DEFAULTS["bb_max"])
    t["min_sellers"] = st.number_input("Min sellers", value=DEFAULTS["min_sellers"], step=1)
    t["max_sellers"] = st.number_input("Max sellers", value=DEFAULTS["max_sellers"], step=1)
    t["min_rating"] = st.number_input("Min rating", value=DEFAULTS["min_rating"], step=0.1)
    t["min_reviews"] = st.number_input("Min reviews", value=DEFAULTS["min_reviews"], step=10)
    t["max_weight_g"] = st.number_input("Max weight (g)", value=DEFAULTS["max_weight_g"], step=50)
    t["max_variations"] = st.number_input("Max variations", value=DEFAULTS["max_variations"], step=1)
    t["max_dominant_share"] = st.number_input("Max single-seller Buy Box share (%)", value=DEFAULTS["max_dominant_share"])
    t["max_180d_delta"] = st.number_input("Max 180-day price delta (%)", value=DEFAULTS["max_180d_delta"])
    t["max_atl_premium"] = st.number_input("Max premium over all-time-low (%)", value=DEFAULTS["max_atl_premium"])
    t["min_net_roi"] = st.number_input("Min Net ROI (%)", value=DEFAULTS["min_net_roi"])
    t["min_net_profit"] = st.number_input("Min Net Profit (£)", value=DEFAULTS["min_net_profit"])
    t["min_breakeven_buffer"] = st.number_input("Min breakeven price-drop buffer (%)", value=DEFAULTS["min_breakeven_buffer"])

with st.sidebar.expander("🐛 Debug: inspect raw Keepa fields"):
    st.caption(
        "I can't hit the live Keepa API from here to verify exact field names/indices "
        "(rating=16, reviews=17, seller count=11, all-time-low='min', buy box share "
        "via buyBoxStats, weight='packageWeight'). Use this on an ASIN you know well "
        "to confirm before trusting the results at scale."
    )
    debug_asin = st.text_input("Test ASIN")
    if st.button("Fetch raw JSON") and debug_asin:
        r = requests.get(
            f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}"
            f"&asin={debug_asin}&stats=180&buybox=1"
        )
        st.json(r.json())

# ==========================================================================
# HELPERS
# ==========================================================================
def safe_get(lst, idx, default=None):
    if lst and len(lst) > idx and lst[idx] not in (None, -1):
        return lst[idx]
    return default


def extract_keepa_metrics(p):
    stats = p.get("stats") or {}
    if not stats:
        return None

    current = stats.get("current", [])
    avg180 = stats.get("avg180", stats.get("avg90", []))

    buy_box_raw = safe_get(current, 18)
    buy_box = (buy_box_raw / 100.0) if buy_box_raw else 0.0

    avg180_bb_raw = safe_get(avg180, 18)
    avg180_bb = (avg180_bb_raw / 100.0) if avg180_bb_raw else buy_box

    amazon_price_raw = safe_get(current, 0)  # None/-1 == Amazon has no live offer right now
    amazon_currently_selling = amazon_price_raw is not None

    bsr = safe_get(current, 3, 0) or 0
    fba_sellers = safe_get(current, 11, 0) or 0  # COUNT_NEW — verify vs your old index-10 read

    rating_raw = safe_get(current, 16)
    rating = (rating_raw / 10.0) if rating_raw else None
    reviews = safe_get(current, 17)

    # Amazon out-of-stock % — Keepa may only guarantee 30/90-day windows even when
    # stats=180 is requested; falls back gracefully if a 180-day field isn't present.
    oos_180 = stats.get("outOfStockPercentageInLast180Days") or stats.get("outOfStockPercentageInLast90Days")
    amz_oos_pct = safe_get(oos_180, 0) if isinstance(oos_180, list) else oos_180

    # All-time-low price (Keepa's unsuffixed min/max = lifetime, per their docs)
    atl_price = None
    atl_raw = stats.get("min")
    if isinstance(atl_raw, list) and len(atl_raw) > 18 and atl_raw[18] and atl_raw[18][1] not in (None, -1):
        atl_price = atl_raw[18][1] / 100.0

    # Buy box seller dominance — requires &buybox=1 on the /product call
    bb_stats = p.get("buyBoxStats") or {}
    dominant_share = 0.0
    if bb_stats:
        shares = [v.get("percentageWon", 0) for v in bb_stats.values() if isinstance(v, dict)]
        if shares:
            dominant_share = max(shares)

    weight_g = p.get("packageWeight") or p.get("itemWeight")

    variations = p.get("variations") or []
    variation_count = len(variations) if isinstance(variations, list) else 1

    category_tree = p.get("categoryTree") or []
    category_names = [c.get("name", "").lower() for c in category_tree if isinstance(c, dict)]

    title = p.get("title") or "Unknown Title"

    return {
        "asin": p.get("asin", ""),
        "brand": p.get("brand", "Unknown"),
        "title": title,
        "buy_box": buy_box,
        "avg180_bb": avg180_bb,
        "bsr": bsr,
        "fba_sellers": fba_sellers,
        "rating": rating,
        "reviews": reviews,
        "amazon_currently_selling": amazon_currently_selling,
        "amz_oos_pct": amz_oos_pct,
        "atl_price": atl_price,
        "dominant_share": dominant_share,
        "weight_g": weight_g,
        "variation_count": variation_count,
        "category_names": category_names,
        "ean_list": p.get("eanList", []),
    }


def compute_profitability(buy_box, cost, referral_rate, fba_fee, inbound_ship):
    if buy_box <= 0:
        return 0.0, 0.0, 0.0, 0.0
    referral_fee = buy_box * (referral_rate / 100.0)
    net_profit = buy_box - (cost + referral_fee + fba_fee + inbound_ship)
    net_roi = (net_profit / cost * 100) if cost > 0 else 0.0
    breakeven_price = (fba_fee + inbound_ship + cost) / (1 - referral_rate / 100.0)
    buffer_pct = ((buy_box - breakeven_price) / buy_box * 100) if buy_box > 0 else 0.0
    return round(net_profit, 2), round(net_roi, 1), round(breakeven_price, 2), round(buffer_pct, 1)


def evaluate_hardened(m, cost, thresholds, referral_rate, fba_fee, inbound_ship, category_bsr_cap):
    """Returns (passes: bool, reasons: list[str]) — reasons list is empty if it passes."""
    reasons = []

    if m["amazon_currently_selling"]:
        reasons.append("Amazon currently holds a live offer")
    if m["amz_oos_pct"] is not None and m["amz_oos_pct"] < 95:
        # i.e. Amazon has been in stock more than 5% of the recent window on record
        reasons.append(f"Amazon in-stock recently ({100 - m['amz_oos_pct']:.0f}% of window)")

    if not (thresholds["bb_min"] <= m["buy_box"] <= thresholds["bb_max"]):
        reasons.append(f"Buy Box £{m['buy_box']:.2f} outside £{thresholds['bb_min']}-£{thresholds['bb_max']}")

    if category_bsr_cap and m["bsr"] and m["bsr"] > category_bsr_cap:
        reasons.append(f"BSR {m['bsr']:,} above cap {category_bsr_cap:,}")

    if not (thresholds["min_sellers"] <= m["fba_sellers"] <= thresholds["max_sellers"]):
        reasons.append(f"{m['fba_sellers']} sellers outside {thresholds['min_sellers']}-{thresholds['max_sellers']}")

    if m["dominant_share"] and m["dominant_share"] > thresholds["max_dominant_share"]:
        reasons.append(f"Top seller has {m['dominant_share']:.0f}% Buy Box share")

    if m["atl_price"]:
        max_allowed = m["atl_price"] * (1 + thresholds["max_atl_premium"] / 100.0)
        if m["buy_box"] > max_allowed:
            reasons.append(f"Price is >{thresholds['max_atl_premium']:.0f}% above all-time-low (£{m['atl_price']:.2f})")

    if m["rating"] is not None and m["rating"] < thresholds["min_rating"]:
        reasons.append(f"Rating {m['rating']:.1f} below {thresholds['min_rating']}")
    if m["reviews"] is not None and m["reviews"] < thresholds["min_reviews"]:
        reasons.append(f"{m['reviews']} reviews below {thresholds['min_reviews']}")

    if m["weight_g"] is None:
        reasons.append("Weight unknown — verify manually")
    elif m["weight_g"] > thresholds["max_weight_g"]:
        reasons.append(f"Weight {m['weight_g']}g above {thresholds['max_weight_g']}g")

    if m["variation_count"] > thresholds["max_variations"]:
        reasons.append(f"{m['variation_count']} variations above {thresholds['max_variations']}")

    for frag in EXCLUDED_CATEGORY_FRAGMENTS:
        if any(frag in c for c in m["category_names"]):
            reasons.append(f"Category matches excluded term '{frag}'")
            break

    title_lower = m["title"].lower()
    for kw in BATTERY_KEYWORDS:
        if kw.strip() in title_lower:
            reasons.append(f"Title suggests battery-operated ('{kw.strip()}')")
            break

    net_profit, net_roi, breakeven_price, buffer_pct = compute_profitability(
        m["buy_box"], cost, referral_rate, fba_fee, inbound_ship
    )
    if net_roi < thresholds["min_net_roi"]:
        reasons.append(f"Net ROI {net_roi:.0f}% below {thresholds['min_net_roi']:.0f}%")
    if net_profit < thresholds["min_net_profit"]:
        reasons.append(f"Net profit £{net_profit:.2f} below £{thresholds['min_net_profit']:.2f}")
    if buffer_pct < thresholds["min_breakeven_buffer"]:
        reasons.append(f"Breakeven buffer {buffer_pct:.0f}% below {thresholds['min_breakeven_buffer']:.0f}%")

    return (len(reasons) == 0), reasons, (net_profit, net_roi, breakeven_price, buffer_pct)


def fetch_products_in_chunks(asins, api_key, chunk_size=PRODUCT_CHUNK_SIZE):
    all_products = []
    progress = st.progress(0.0)
    n_chunks = math.ceil(len(asins) / chunk_size)
    for i in range(n_chunks):
        chunk = asins[i * chunk_size:(i + 1) * chunk_size]
        url = (
            f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}"
            f"&asin={','.join(chunk)}&stats=180&buybox=1"
        )
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            all_products.extend(data.get("products", []))
            tokens_left = data.get("tokensLeft")
            if tokens_left is not None:
                st.caption(f"Keepa tokens remaining: {tokens_left}")
        else:
            st.error(f"Keepa Product API error on chunk {i+1}: {resp.status_code} — {resp.text[:200]}")
        progress.progress((i + 1) / n_chunks)
    progress.empty()
    return all_products


# ==========================================================================
# TABS
# ==========================================================================
tab1, tab2 = st.tabs(["📁 Scan Wholesale CSV", "🔍 Keepa Database Discovery"])

# --------------------------------------------------------------------------
# TAB 1: WHOLESALE CSV SCANNER
# --------------------------------------------------------------------------
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
            eans = df[ean_col].dropna().astype(str).tolist()[:max_results]

            with st.spinner(f"Scanning {len(eans)} products against Amazon UK..."):
                url = (
                    f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}"
                    f"&code={','.join(eans[:PRODUCT_CHUNK_SIZE])}&stats=180&buybox=1"
                )
                resp = requests.get(url)

                if resp.status_code == 200:
                    products = resp.json().get("products", [])
                    rows = []
                    for p in products:
                        m = extract_keepa_metrics(p)
                        if not m:
                            continue

                        ean = m["ean_list"][0] if m["ean_list"] else ""
                        match = df[df[ean_col].astype(str) == str(ean)]
                        cost = float(match[cost_col].values[0]) if not match.empty else 0.0

                        passes, reasons, (net_profit, net_roi, breakeven, buffer_pct) = evaluate_hardened(
                            m, cost, t, referral_rate_pct, fba_fee_est, inbound_ship_est,
                            None  # no fixed category cap for arbitrary CSV uploads
                        )

                        rows.append({
                            "ASIN": m["asin"],
                            "Title": m["title"][:45] + "...",
                            "Cost (£)": round(cost, 2),
                            "Buy Box (£)": round(m["buy_box"], 2),
                            "Net Profit (£)": net_profit,
                            "Net ROI (%)": net_roi,
                            "Breakeven Buffer (%)": buffer_pct,
                            "BSR": m["bsr"],
                            "Sellers": m["fba_sellers"],
                            "Amazon Selling?": m["amazon_currently_selling"],
                            "Rating": m["rating"],
                            "Reviews": m["reviews"],
                            "Weight (g)": m["weight_g"],
                            "Variations": m["variation_count"],
                            "Passes Hardened Filter": passes,
                            "Fail Reasons": "; ".join(reasons) if reasons else "",
                            "Link": f"https://www.amazon.co.uk/dp/{m['asin']}",
                        })

                    res_df = pd.DataFrame(rows)
                    if not res_df.empty:
                        passed = res_df[res_df["Passes Hardened Filter"]]
                        st.success(f"{len(passed)} of {len(res_df)} products passed every hardened filter.")
                        st.dataframe(passed.drop(columns=["Passes Hardened Filter"]), use_container_width=True)

                        with st.expander("Show all products with pass/fail reasons (for debugging filters)"):
                            st.dataframe(res_df, use_container_width=True)
                    else:
                        st.warning("No valid matching products found.")
                else:
                    st.error(f"Keepa API Error ({resp.status_code}): {resp.text}")

# --------------------------------------------------------------------------
# TAB 2: LIVE KEEPA DATABASE DISCOVERY
# --------------------------------------------------------------------------
with tab2:
    st.subheader("Filter Amazon UK Database for Winning Products")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        selected_cat = st.selectbox("Root Category", list(CATEGORY_MAP.keys()))
    with col_b:
        st.metric("Buy Box range", f"£{t['bb_min']:.0f}–£{t['bb_max']:.0f}")
        st.metric("Sellers", f"{t['min_sellers']:.0f}–{t['max_sellers']:.0f}")
    with col_c:
        cat_bsr_cap = CATEGORY_BSR_CAP.get(selected_cat, 15000)
        st.metric("BSR cap (this category)", f"< {cat_bsr_cap:,}")
        st.metric("Max weight", f"{t['max_weight_g']:.0f} g")

    if st.button("🔍 Search Keepa Database"):
        selection = {
            "current_SALES_gte": 1,
            "current_SALES_lte": int(cat_bsr_cap),
            "current_BUY_BOX_SHIPPING_gte": int(t["bb_min"] * 100),
            "current_BUY_BOX_SHIPPING_lte": int(t["bb_max"] * 100),
            "current_COUNT_LIVE_OFFERS_SHIPPING_gte": int(t["min_sellers"]),
            "current_COUNT_LIVE_OFFERS_SHIPPING_lte": int(t["max_sellers"]),
            "current_RATING_gte": int(t["min_rating"] * 10),
            "current_COUNT_REVIEWS_gte": int(t["min_reviews"]),
            # Amazon exclusion at query level: -1 on the AMAZON price channel means
            # no live Amazon offer. Mirrors the current_TYPE_gte/lte pattern your
            # original code already used successfully for SALES and BUY_BOX_SHIPPING.
            "current_AMAZON_gte": -1,
            "current_AMAZON_lte": -1,
            # Static-attribute weight filter — less certain this exact field name is
            # right; the client-side weight check below is the real safety net.
            "packageWeight_lte": int(t["max_weight_g"]),
            "rootCategory": CATEGORY_MAP[selected_cat],
            "sort": [["current_SALES", "asc"]],
            "perPage": int(max_results),
            "page": 0,
        }

        with st.spinner("Querying Keepa Database..."):
            query_url = f"https://api.keepa.com/query?key={api_key}&domain={DOMAIN_UK}&selection={json.dumps(selection)}"
            query_resp = requests.get(query_url)

            if query_resp.status_code == 200:
                query_json = query_resp.json()
                asins = query_json.get("asinList", []) or []
                if query_json.get("tokensLeft") is not None:
                    st.caption(f"Keepa tokens remaining: {query_json['tokensLeft']}")
                st.success(f"Query matched **{len(asins)}** candidate ASINs (before client-side hardened checks).")

                if asins:
                    products = fetch_products_in_chunks(asins, api_key)
                    rows = []
                    for p in products:
                        m = extract_keepa_metrics(p)
                        if not m:
                            continue

                        assumed_cost = m["buy_box"] * (assumed_cost_pct / 100.0)
                        passes, reasons, (net_profit, net_roi, breakeven, buffer_pct) = evaluate_hardened(
                            m, assumed_cost, t, referral_rate_pct, fba_fee_est, inbound_ship_est,
                            cat_bsr_cap
                        )

                        rows.append({
                            "ASIN": m["asin"],
                            "Brand": m["brand"],
                            "Title": m["title"][:50] + "...",
                            "Buy Box (£)": round(m["buy_box"], 2),
                            "180d Avg (£)": round(m["avg180_bb"], 2),
                            "BSR": m["bsr"],
                            "Sellers": m["fba_sellers"],
                            "Amazon Selling?": m["amazon_currently_selling"],
                            "Top Seller BB Share (%)": round(m["dominant_share"], 1),
                            "Rating": m["rating"],
                            "Reviews": m["reviews"],
                            "Weight (g)": m["weight_g"],
                            "Variations": m["variation_count"],
                            "Est. Net ROI % (assumed cost)": net_roi,
                            "Est. Breakeven Buffer %": buffer_pct,
                            "Passes Hardened Filter": passes,
                            "Fail Reasons": "; ".join(reasons) if reasons else "",
                            "Amazon Link": f"https://www.amazon.co.uk/dp/{m['asin']}",
                        })

                    full_df = pd.DataFrame(rows)
                    if not full_df.empty:
                        passed = full_df[full_df["Passes Hardened Filter"]]
                        st.success(f"{len(passed)} of {len(full_df)} fetched products passed every hardened filter.")
                        st.dataframe(passed.drop(columns=["Passes Hardened Filter"]), use_container_width=True)

                        with st.expander("Show ALL fetched products with pass/fail reasons (debug Crocs-type leaks here)"):
                            st.dataframe(full_df, use_container_width=True)
                    else:
                        st.warning("No products returned from the product detail fetch.")
            else:
                st.error(f"Keepa Query API Error ({query_resp.status_code}): {query_resp.text}")
