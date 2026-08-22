import streamlit as st
import pandas as pd
import requests
import json
import math
import time

st.set_page_config(layout="wide", page_title="Amazon UK Wholesale Hub")
st.title("🇬🇧 Amazon UK Wholesale Sourcing & Analytics Hub")

DOMAIN_UK = 2

# ==========================================================================
# HARDENED "ZERO-RISK" FILTER DEFAULTS
# ==========================================================================
DEFAULTS = {
    "bb_min": 22.0,
    "bb_max": 55.0,
    "min_sellers": 3,
    "max_sellers": 8,
    "min_rating": 4.4,
    "min_reviews": 250,
    "max_weight_g": 800,
    "max_variations": 4,
    "max_dominant_share": 35.0,
    "max_180d_delta": 5.0,
    "max_atl_premium": 15.0,
    "min_net_roi": 30.0,
    "min_net_profit": 5.0,
    "min_breakeven_buffer": 20.0,
}

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

BATTERY_KEYWORDS = [
    "battery", "batteries", "rechargeable", "cordless", "lithium",
    " aa ", " aaa ", "9v", "cr2032", "power bank", "solar powered",
]
EXCLUDED_CATEGORY_FRAGMENTS = [
    "clothing", "apparel", "shoes", "jewellery", "jewelry",
    "grocery", "food", "gourmet", "clothes",
]

PRODUCT_CHUNK_SIZE = 50
DEFAULT_TOKENS_PER_PRODUCT_NO_BB = 1.5  # prior estimate until we observe a real number
DEFAULT_TOKENS_PER_PRODUCT_BB = 3.0     # observed from your last run: 300 tokens / 100 products

# ==========================================================================
# SIDEBAR
# ==========================================================================
st.sidebar.header("🔑 API Settings")
api_key = st.sidebar.text_input("Enter Keepa API Key", type="password")

if not api_key:
    st.info("👈 Please enter your Keepa API Key in the sidebar to get started.")
    st.stop()

# ---- session state init ----
defaults_state = {
    "last_tokens_left": None,
    "asin_cache": {},              # asin -> metrics dict, accumulates across the whole session
    "stage1_metrics": [],          # metrics list from the most recent Stage 1 run
    "stage1_provisional_asins": [],
    "stage1_category": None,
    "stage1_bsr_cap": None,
    "stage2_metrics": [],
    "obs_rate_no_bb": None,        # empirically observed tokens/product, no buybox
    "obs_rate_bb": None,           # empirically observed tokens/product, with buybox
}
for k, v in defaults_state.items():
    if k not in st.session_state:
        st.session_state[k] = v

col_tok1, col_tok2, col_tok3 = st.sidebar.columns(3)
col_tok1.metric("Tokens left", st.session_state["last_tokens_left"] if st.session_state["last_tokens_left"] is not None else "—")
col_tok2.metric("£/product (no BB)", f"{st.session_state['obs_rate_no_bb']:.1f}" if st.session_state["obs_rate_no_bb"] else "—")
col_tok3.metric("£/product (+BB)", f"{st.session_state['obs_rate_bb']:.1f}" if st.session_state["obs_rate_bb"] else "—")
st.sidebar.caption(f"Cached ASINs this session: **{len(st.session_state['asin_cache'])}** (never re-fetched)")
if st.sidebar.button("🗑️ Clear cache (force fresh data on next fetch)"):
    st.session_state["asin_cache"] = {}
    st.session_state["stage1_metrics"] = []
    st.session_state["stage1_provisional_asins"] = []
    st.session_state["stage2_metrics"] = []
    st.rerun()

st.sidebar.header("⚙️ Result Volume")
max_results = st.sidebar.slider(
    "Max products per search", min_value=50, max_value=500, value=250, step=50,
    help="How many ASINs the query stage matches. Only ASINs not already in the "
         "session cache will actually cost tokens to fetch."
)

st.sidebar.header("💷 Fee Assumptions")
referral_rate_pct = st.sidebar.number_input(
    "Referral fee % for this category", min_value=5.0, max_value=20.0, value=15.0, step=0.5
)
fba_fee_est = st.sidebar.number_input("Est. FBA pick & pack fee (£)", value=2.85, step=0.05)
inbound_ship_est = st.sidebar.number_input("Est. inbound shipping/prep per unit (£)", value=0.35, step=0.05)

st.sidebar.header("💰 Indicative Cost (Discovery tab)")
assumed_cost_pct = st.sidebar.slider(
    "Assumed cost as % of Buy Box", min_value=20, max_value=70, value=45, step=5,
    help="Retuning this never costs tokens — hit 'Re-apply filters' to recalc instantly."
)

st.sidebar.header("🎚️ Hardened Filter Thresholds")
with st.sidebar.expander("Adjust thresholds (retuning is always free — no API calls)"):
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
        "Field names/indices I can't verify without hitting the live API myself "
        "(rating=16, reviews=17, sellers=11, all-time-low='min', buy box share via "
        "buyBoxStats, weight='packageWeight') — check against a known ASIN here."
    )
    debug_asin = st.text_input("Test ASIN")
    if st.button("Fetch raw JSON") and debug_asin:
        r = requests.get(
            f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}"
            f"&asin={debug_asin}&stats=180&buybox=1"
        )
        st.json(r.json())

# ==========================================================================
# KEEPA REQUEST HELPERS
# ==========================================================================
def keepa_get(url, max_retries=5, max_total_wait_s=300):
    waited = 0.0
    resp = None
    for attempt in range(max_retries):
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("tokensLeft") is not None:
                st.session_state["last_tokens_left"] = data["tokensLeft"]
            return resp, None
        if resp.status_code == 429:
            try:
                refill_ms = resp.json().get("refillIn", 30000)
            except Exception:
                refill_ms = 30000
            wait_s = max(refill_ms / 1000.0, 1)
            if waited + wait_s > max_total_wait_s:
                return resp, (
                    f"Still rate-limited after {waited:.0f}s. Keepa wants another "
                    f"{wait_s:.0f}s — try again shortly, or fetch fewer new ASINs."
                )
            st.warning(f"⏳ Rate limited — waiting {wait_s:.0f}s for tokens to refill (attempt {attempt+1}/{max_retries})...")
            time.sleep(wait_s)
            waited += wait_s
            continue
        return resp, f"Keepa API error {resp.status_code}: {resp.text[:200]}"
    return resp, "Gave up after repeated rate limiting."


def fetch_raw_products(asins, api_key, use_buybox, chunk_size=PRODUCT_CHUNK_SIZE):
    """Fetches ONLY the given ASINs from Keepa (caller is responsible for having
    already stripped out anything already cached). Returns (products, total_tokens_consumed)."""
    all_products = []
    total_consumed = 0
    if not asins:
        return all_products, total_consumed
    progress = st.progress(0.0)
    n_chunks = math.ceil(len(asins) / chunk_size)
    for i in range(n_chunks):
        chunk = asins[i * chunk_size:(i + 1) * chunk_size]
        bb_param = "&buybox=1" if use_buybox else ""
        url = (
            f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}"
            f"&asin={','.join(chunk)}&stats=180{bb_param}"
        )
        resp, err = keepa_get(url)
        if err:
            st.error(f"Chunk {i+1}/{n_chunks} ({len(chunk)} ASINs): {err}")
            progress.progress((i + 1) / n_chunks)
            break
        data = resp.json()
        all_products.extend(data.get("products", []))
        consumed = data.get("tokensConsumed", 0) or 0
        total_consumed += consumed
        per_product = (consumed / len(chunk)) if chunk else 0
        st.caption(
            f"Chunk {i+1}/{n_chunks}: {len(chunk)} NEW products fetched, "
            f"{consumed} tokens consumed (~{per_product:.2f}/product), "
            f"{data.get('tokensLeft')} tokens left."
        )
        progress.progress((i + 1) / n_chunks)
    progress.empty()
    return all_products, total_consumed


def get_metrics_for_asins(asins, api_key, need_buybox):
    """The core token-saving layer: only fetches ASINs that aren't already
    cached (or that are cached but missing buybox data if buybox is needed).
    Everything else is served from st.session_state['asin_cache'] for free."""
    cache = st.session_state["asin_cache"]
    to_fetch = [a for a in asins if (a not in cache) or (need_buybox and not cache[a].get("has_buybox_data"))]
    n_cached = len(asins) - len(to_fetch)

    if n_cached:
        st.caption(f"✅ {n_cached} of {len(asins)} ASINs served from cache — 0 tokens.")

    if to_fetch:
        rate_key = "obs_rate_bb" if need_buybox else "obs_rate_no_bb"
        prior = st.session_state[rate_key] or (DEFAULT_TOKENS_PER_PRODUCT_BB if need_buybox else DEFAULT_TOKENS_PER_PRODUCT_NO_BB)
        st.info(f"Fetching {len(to_fetch)} new ASINs — estimated ~{len(to_fetch) * prior:.0f} tokens based on this session's observed rate.")

        products, consumed = fetch_raw_products(to_fetch, api_key, use_buybox=need_buybox)
        fetched_count = 0
        for p in products:
            m = extract_keepa_metrics(p)
            if not m:
                continue
            existing = cache.get(m["asin"], {})
            existing.update(m)  # buybox fetch on top of an existing no-bb entry upgrades it in place
            cache[m["asin"]] = existing
            fetched_count += 1

        if fetched_count:
            observed = consumed / fetched_count
            # exponential moving average so one weird chunk doesn't swing the estimate wildly
            prev = st.session_state[rate_key]
            st.session_state[rate_key] = observed if prev is None else (0.5 * prev + 0.5 * observed)
            st.caption(f"Updated observed rate: ~{st.session_state[rate_key]:.2f} tokens/product ({'with' if need_buybox else 'without'} Buy Box data).")

    return [cache[a] for a in asins if a in cache]


# ==========================================================================
# METRIC EXTRACTION & FILTER EVALUATION
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

    amazon_price_raw = safe_get(current, 0)
    amazon_currently_selling = amazon_price_raw is not None

    bsr = safe_get(current, 3, 0) or 0
    fba_sellers = safe_get(current, 11, 0) or 0

    rating_raw = safe_get(current, 16)
    rating = (rating_raw / 10.0) if rating_raw else None
    reviews = safe_get(current, 17)

    oos_180 = stats.get("outOfStockPercentageInLast180Days") or stats.get("outOfStockPercentageInLast90Days")
    amz_oos_pct = safe_get(oos_180, 0) if isinstance(oos_180, list) else oos_180

    atl_price = None
    atl_raw = stats.get("min")
    if isinstance(atl_raw, list) and len(atl_raw) > 18 and atl_raw[18] and atl_raw[18][1] not in (None, -1):
        atl_price = atl_raw[18][1] / 100.0

    bb_stats = p.get("buyBoxStats") or {}
    dominant_share = 0.0
    has_buybox_data = bool(bb_stats)
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
        "has_buybox_data": has_buybox_data,
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


def evaluate_hardened(m, cost, thresholds, referral_rate, fba_fee, inbound_ship,
                       category_bsr_cap, check_dominant_share=True):
    reasons = []

    if m["amazon_currently_selling"]:
        reasons.append("Amazon currently holds a live offer")
    if m["amz_oos_pct"] is not None and m["amz_oos_pct"] < 95:
        reasons.append(f"Amazon in-stock recently ({100 - m['amz_oos_pct']:.0f}% of window)")

    if not (thresholds["bb_min"] <= m["buy_box"] <= thresholds["bb_max"]):
        reasons.append(f"Buy Box £{m['buy_box']:.2f} outside £{thresholds['bb_min']}-£{thresholds['bb_max']}")

    if category_bsr_cap and m["bsr"] and m["bsr"] > category_bsr_cap:
        reasons.append(f"BSR {m['bsr']:,} above cap {category_bsr_cap:,}")

    if not (thresholds["min_sellers"] <= m["fba_sellers"] <= thresholds["max_sellers"]):
        reasons.append(f"{m['fba_sellers']} sellers outside {thresholds['min_sellers']}-{thresholds['max_sellers']}")

    if check_dominant_share:
        if not m["has_buybox_data"]:
            reasons.append("Buy Box share data not fetched yet")
        elif m["dominant_share"] > thresholds["max_dominant_share"]:
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


def build_row(m, net_profit, net_roi, buffer_pct, passes, reasons, brand_col=True):
    row = {"ASIN": m["asin"]}
    if brand_col:
        row["Brand"] = m["brand"]
    row.update({
        "Title": m["title"][:50] + "...",
        "Buy Box (£)": round(m["buy_box"], 2),
        "180d Avg (£)": round(m["avg180_bb"], 2),
        "BSR": m["bsr"],
        "Sellers": m["fba_sellers"],
        "Amazon Selling?": m["amazon_currently_selling"],
        "Top Seller BB Share (%)": (round(m["dominant_share"], 1) if m["has_buybox_data"] else "not checked yet"),
        "Rating": m["rating"],
        "Reviews": m["reviews"],
        "Weight (g)": m["weight_g"],
        "Variations": m["variation_count"],
        "Net ROI (%)": net_roi,
        "Breakeven Buffer (%)": buffer_pct,
        "Passes Filter": passes,
        "Fail/Pending Reasons": "; ".join(reasons) if reasons else "",
        "Amazon Link": f"https://www.amazon.co.uk/dp/{m['asin']}",
    })
    return row


def build_table(metrics_list, category_bsr_cap, check_dominant_share):
    """Pure client-side recompute — takes already-fetched metrics, applies CURRENT
    threshold/fee/cost-assumption values from the sidebar, zero API calls."""
    rows, provisional = [], []
    for m in metrics_list:
        assumed_cost = m["buy_box"] * (assumed_cost_pct / 100.0)
        passes, reasons, (net_profit, net_roi, breakeven, buffer_pct) = evaluate_hardened(
            m, assumed_cost, t, referral_rate_pct, fba_fee_est, inbound_ship_est,
            category_bsr_cap, check_dominant_share=check_dominant_share
        )
        rows.append(build_row(m, net_profit, net_roi, buffer_pct, passes, reasons))
        if passes:
            provisional.append(m["asin"])
    return pd.DataFrame(rows), provisional


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
    include_buybox_tab1 = st.checkbox(
        "Include Buy Box dominance check (extra tokens)", value=False,
        help="Leave off for a first pass over a big sheet — turn on only once "
             "you've narrowed to a shortlist worth the extra spend."
    )

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
                bb_param = "&buybox=1" if include_buybox_tab1 else ""
                url = (
                    f"https://api.keepa.com/product?key={api_key}&domain={DOMAIN_UK}"
                    f"&code={','.join(eans[:PRODUCT_CHUNK_SIZE])}&stats=180{bb_param}"
                )
                resp, err = keepa_get(url)
                if err:
                    st.error(err)
                else:
                    products = resp.json().get("products", [])
                    metrics_list = []
                    cost_by_asin = {}
                    for p in products:
                        m = extract_keepa_metrics(p)
                        if not m:
                            continue
                        st.session_state["asin_cache"][m["asin"]] = m
                        metrics_list.append(m)
                        ean = m["ean_list"][0] if m["ean_list"] else ""
                        match = df[df[ean_col].astype(str) == str(ean)]
                        cost_by_asin[m["asin"]] = float(match[cost_col].values[0]) if not match.empty else 0.0

                    rows = []
                    for m in metrics_list:
                        cost = cost_by_asin.get(m["asin"], 0.0)
                        passes, reasons, (net_profit, net_roi, breakeven, buffer_pct) = evaluate_hardened(
                            m, cost, t, referral_rate_pct, fba_fee_est, inbound_ship_est,
                            None, check_dominant_share=include_buybox_tab1
                        )
                        row = build_row(m, net_profit, net_roi, buffer_pct, passes, reasons)
                        row["Cost (£)"] = round(cost, 2)
                        rows.append(row)

                    res_df = pd.DataFrame(rows)
                    if not res_df.empty:
                        passed = res_df[res_df["Passes Filter"]]
                        st.success(f"{len(passed)} of {len(res_df)} products passed every filter.")
                        st.dataframe(passed.drop(columns=["Passes Filter"]), use_container_width=True)
                        with st.expander("Show all products with pass/fail reasons"):
                            st.dataframe(res_df, use_container_width=True)
                    else:
                        st.warning("No valid matching products found.")

# --------------------------------------------------------------------------
# TAB 2: LIVE KEEPA DATABASE DISCOVERY — cache-first, two-stage buybox deferral
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

    st.caption(
        "**Stage 1** fetches only what isn't already cached (no Buy Box dominance data — "
        "that costs extra). **Stage 2** checks Buy Box dominance only for Stage 1 survivors. "
        "**Re-apply filters** recalculates everything from cached data for free — use it "
        "whenever you tweak a threshold instead of re-searching."
    )

    c1, c2 = st.columns(2)
    with c1:
        run_stage1 = st.button("🔍 Stage 1: Fetch (new ASINs only)")
    with c2:
        reapply_stage1 = st.button("🔄 Re-apply filters to fetched data (free, no API calls)")

    if run_stage1:
        selection = {
            "current_SALES_gte": 1,
            "current_SALES_lte": int(cat_bsr_cap),
            "current_BUY_BOX_SHIPPING_gte": int(t["bb_min"] * 100),
            "current_BUY_BOX_SHIPPING_lte": int(t["bb_max"] * 100),
            "current_COUNT_LIVE_OFFERS_SHIPPING_gte": int(t["min_sellers"]),
            "current_COUNT_LIVE_OFFERS_SHIPPING_lte": int(t["max_sellers"]),
            "current_RATING_gte": int(t["min_rating"] * 10),
            "current_COUNT_REVIEWS_gte": int(t["min_reviews"]),
            "current_AMAZON_gte": -1,
            "current_AMAZON_lte": -1,
            "packageWeight_lte": int(t["max_weight_g"]),
            "rootCategory": CATEGORY_MAP[selected_cat],
            "sort": [["current_SALES", "asc"]],
            "perPage": int(max_results),
            "page": 0,
        }
        with st.spinner("Querying Keepa Database..."):
            query_resp, err = keepa_get(
                f"https://api.keepa.com/query?key={api_key}&domain={DOMAIN_UK}&selection={json.dumps(selection)}"
            )
            if err:
                st.error(err)
            else:
                asins = query_resp.json().get("asinList", []) or []
                st.success(f"Query matched **{len(asins)}** candidate ASINs.")
                if asins:
                    metrics_list = get_metrics_for_asins(asins, api_key, need_buybox=False)
                    st.session_state["stage1_metrics"] = metrics_list
                    st.session_state["stage1_category"] = selected_cat
                    st.session_state["stage1_bsr_cap"] = cat_bsr_cap

    if reapply_stage1 and not st.session_state["stage1_metrics"]:
        st.warning("Nothing fetched yet this session — run Stage 1 at least once first.")

    if st.session_state["stage1_metrics"]:
        df1, provisional = build_table(
            st.session_state["stage1_metrics"],
            st.session_state["stage1_bsr_cap"],
            check_dominant_share=False,
        )
        st.session_state["stage1_provisional_asins"] = provisional
        passed1 = df1[df1["Passes Filter"]]
        st.success(
            f"Stage 1 ({st.session_state['stage1_category']}): {len(passed1)} of {len(df1)} "
            f"cached products pass the cheap screen."
        )
        st.dataframe(passed1.drop(columns=["Passes Filter"]), use_container_width=True)
        with st.expander("Show ALL Stage 1 products with pass/fail reasons"):
            st.dataframe(df1, use_container_width=True)

    provisional_asins = st.session_state["stage1_provisional_asins"]
    if provisional_asins:
        already_have_bb = sum(1 for a in provisional_asins if st.session_state["asin_cache"].get(a, {}).get("has_buybox_data"))
        st.info(
            f"{len(provisional_asins)} products provisionally passed Stage 1 — "
            f"{already_have_bb} already have Buy Box data cached, "
            f"{len(provisional_asins) - already_have_bb} would need a new fetch."
        )
        c3, c4 = st.columns(2)
        with c3:
            run_stage2 = st.button(f"🔎 Stage 2: Buy Box check ({len(provisional_asins) - already_have_bb} new fetches)")
        with c4:
            reapply_stage2 = st.button("🔄 Re-apply filters to Stage 2 data (free)")

        if run_stage2:
            metrics2 = get_metrics_for_asins(provisional_asins, api_key, need_buybox=True)
            st.session_state["stage2_metrics"] = metrics2

        if reapply_stage2 and not st.session_state["stage2_metrics"]:
            st.warning("Run Stage 2 at least once first.")

        if st.session_state["stage2_metrics"]:
            final_df, _ = build_table(
                st.session_state["stage2_metrics"],
                st.session_state["stage1_bsr_cap"],
                check_dominant_share=True,
            )
            final_passed = final_df[final_df["Passes Filter"]]
            st.success(f"Stage 2: {len(final_passed)} of {len(st.session_state['stage2_metrics'])} also clear the Buy Box dominance check.")
            st.dataframe(final_passed.drop(columns=["Passes Filter"]), use_container_width=True)
            with st.expander("Show ALL Stage 2 products with pass/fail reasons"):
                st.dataframe(final_df, use_container_width=True)
