"""
Spending Review — Main Dashboard
=================================
Run with: streamlit run app.py
"""

import io
import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from categorise import categorise_dataframe, load_mapping, UNCATEGORISED
from categories import load_categories
from accounts import load_accounts
from transaction_history import load_history_mapping, append_to_history, DEFAULT_PATH as HISTORY_PATH
from parsers.format_a import parse as parse_format_a
from parsers.format_b import parse as parse_format_b

# --- Page config ---
st.set_page_config(page_title="Spending Review", layout="wide")

# --- Custom styling (matches Portfolio Allocation Tool) ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=Source+Sans+Pro:wght@300;400;600&display=swap');

    h1, h2, h3 {
        font-family: 'Playfair Display', serif !important;
        color: #3D3229 !important;
        font-weight: 600 !important;
    }
    h1 { letter-spacing: -0.5px; }

    .stMarkdown, .stText, p, label {
        font-family: 'Source Sans Pro', sans-serif !important;
    }

    .stButton > button[kind="primary"] {
        background-color: #8B7355 !important;
        border: none !important;
        color: white !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #74604A !important;
    }
    .stButton > button[kind="secondary"] {
        border-color: #8B7355 !important;
        color: #8B7355 !important;
    }

    [data-testid="stMetricValue"] {
        font-family: 'Playfair Display', serif !important;
        color: #3D3229 !important;
    }

    .stDataFrame { border-radius: 4px; }
    .block-container { padding-top: 2rem !important; }

    /* File uploader — fix overlapping text and match theme */
    [data-testid="stFileUploader"] section {
        background-color: #EDE7DF !important;
        border: 1px dashed #C4B8A8 !important;
        border-radius: 4px !important;
    }
    [data-testid="stFileUploader"] section > button {
        background-color: white !important;
        color: #3D3229 !important;
        border: 1px solid #C4B8A8 !important;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] {
        color: #3D3229 !important;
    }

    .stAlert { border-radius: 4px !important; }

    /* Preserve material icon rendering — icons must use their own font, not Source Sans Pro */
    [data-testid="stIcon"],
    .material-icons,
    .material-symbols-outlined,
    span.material-icons,
    span.material-symbols-outlined {
        font-family: 'Material Symbols Outlined', 'Material Icons' !important;
    }

    /* Dataframe — solid backgrounds for menus and headers
       (prevents text-on-text overlap when column menu opens) */
    .stDataFrame [role="columnheader"] {
        background-color: #EDE7DF !important;
        color: #3D3229 !important;
    }
    [data-testid="stDataFrameResizable"] {
        background-color: white !important;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<h1 style="color:#556B2F !important;">Spending Review</h1>', unsafe_allow_html=True)

# --- Constants ---
MAPPING_PATH = "config/mapping.json"

# Parser registry — add new formats here
PARSERS = {
    "Format A": parse_format_a,
    "Format B": parse_format_b,
}

# Load account → format mapping. Fail loud at startup so misconfiguration
# surfaces immediately rather than mid-flow.
try:
    ACCOUNTS = load_accounts(valid_formats=set(PARSERS.keys()))
except (FileNotFoundError, ValueError) as e:
    st.error(f"**Configuration error:** {e}")
    st.stop()

# --- Session state ---
for key, default in [
    ("compiled", None),
    ("dropped_negatives", 0),
    ("duplicates_removed", 0),
    ("file_accounts", {}),       # file_id → account name
    ("last_account", None),      # last account chosen, used as default for new files
    ("history_warnings", []),    # invalid-category warnings from transaction_history
]:
    if key not in st.session_state:
        st.session_state[key] = default


# --- Helpers ---
def compile_statements(uploaded_files, file_accounts: dict) -> pd.DataFrame:
    """
    Parse all uploaded files using their selected accounts, concatenate,
    dedupe, drop negatives, categorise.

    Args:
        uploaded_files: list of Streamlit UploadedFile objects.
        file_accounts: dict mapping uploader file_id → account name.

    Adds an 'account' column derived from the user's per-file selection.
    """
    frames = []
    for uf in uploaded_files:
        account = file_accounts.get(uf.file_id)
        if account is None or account not in ACCOUNTS:
            st.error(f"No account selected for '{uf.name}'.")
            return None
        format_name = ACCOUNTS[account]
        parser_func = PARSERS[format_name]
        try:
            parsed = parser_func(uf.getvalue(), uf.name)
        except ValueError as e:
            st.error(f"Failed to parse '{uf.name}' as {account} ({format_name}): {e}")
            return None
        parsed["account"] = account
        frames.append(parsed)

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True)

    # Dedupe on (date, amount, description). Account is intentionally NOT in
    # the dedupe key — if the same merchant charge appears in two accounts on
    # the same day for the same amount, that's almost certainly a duplicate
    # (same statement uploaded twice under different account labels), not two
    # genuine charges. Keep first occurrence.
    before = len(df)
    df = df.drop_duplicates(subset=["date", "amount", "description"]).reset_index(drop=True)
    st.session_state.duplicates_removed = before - len(df)

    # Drop refunds / payments
    before = len(df)
    df = df[df["amount"] > 0].reset_index(drop=True)
    st.session_state.dropped_negatives = before - len(df)

    # Categorise. History layer (exact-match) checked before substring layer.
    try:
        mapping = load_mapping(MAPPING_PATH)
    except FileNotFoundError as e:
        st.error(str(e))
        return None

    # Load valid categories so we can validate the history file's contents.
    # If categories.txt is broken, surface the error and skip history validation
    # rather than blocking the whole compile.
    try:
        valid_cats, _ = load_categories()
    except (FileNotFoundError, ValueError):
        valid_cats = None  # validation disabled if categories.txt missing

    try:
        history, history_warnings = load_history_mapping(
            HISTORY_PATH, valid_categories=valid_cats
        )
    except ValueError as e:
        st.error(f"Could not load transaction history: {e}")
        return None

    # Stash warnings in session state so they show every rerun, not just
    # the rerun that triggered Compile.
    st.session_state.history_warnings = history_warnings

    df = categorise_dataframe(df, mapping, history)
    return df


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Serialise a DataFrame to xlsx bytes."""
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def format_sgd(x: float) -> str:
    return f"${x:,.2f}"


# --- Upload section ---
st.header("Upload Statements")

uploaded = st.file_uploader(
    "Drop one or more statement files",
    type=["xlsx", "csv"],
    accept_multiple_files=True,
)

# Garbage-collect account memory for files no longer in the uploader
if uploaded is not None:
    current_ids = {uf.file_id for uf in uploaded}
    st.session_state.file_accounts = {
        fid: acct for fid, acct in st.session_state.file_accounts.items()
        if fid in current_ids
    }

# Render per-file account selectors
if uploaded:
    st.markdown("**Select account per file**")
    account_options = list(ACCOUNTS.keys())

    for uf in uploaded:
        # Default each file's account to: previously chosen for this file >
        # last account the user picked > first account in the config.
        existing = st.session_state.file_accounts.get(uf.file_id)
        default = existing or st.session_state.last_account or account_options[0]
        default_idx = account_options.index(default) if default in account_options else 0

        col_name, col_account = st.columns([3, 1])
        with col_name:
            st.markdown(f"📄 `{uf.name}`")
        with col_account:
            chosen = st.selectbox(
                "Account",
                account_options,
                index=default_idx,
                key=f"account_{uf.file_id}",
                label_visibility="collapsed",
            )
            st.session_state.file_accounts[uf.file_id] = chosen
            st.session_state.last_account = chosen

if st.button("Compile", type="primary", disabled=not uploaded):
    with st.spinner("Parsing and categorising..."):
        result = compile_statements(uploaded, st.session_state.file_accounts)
        if result is not None:
            st.session_state.compiled = result

# --- Results section ---
if st.session_state.compiled is not None:
    df_compiled = st.session_state.compiled

    # Surface invalid-category warnings from transaction_history.xlsx, if any.
    # These rows fall through to substring matching (or Uncategorised) — the
    # dashboard still renders, but the user is told what to fix.
    if st.session_state.history_warnings:
        n = len(st.session_state.history_warnings)
        with st.expander(
            f"⚠️ {n} invalid categor{'y' if n == 1 else 'ies'} in `transaction_history.xlsx`",
            expanded=False,
        ):
            st.markdown(
                "These rows have a category that isn't in `categories.txt`. "
                "They've been ignored — the affected transactions fall through to "
                "substring matching. Fix the typos and re-Compile to apply."
            )
            for w in st.session_state.history_warnings:
                st.markdown(f"- {w}")

    # --- Date range filter (top-level, applies to everything below) ---
    min_date = df_compiled["date"].min()
    max_date = df_compiled["date"].max()

    st.header("Date Range")
    date_range = st.date_input(
        "Filter by date",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        format="YYYY-MM-DD",
        label_visibility="collapsed",
    )

    # st.date_input returns a tuple of (start, end) when value is a 2-tuple,
    # but during partial selection it returns a 1-tuple. Handle both cases.
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        # User is mid-selection (only picked start, not end) — show full range
        start_date, end_date = min_date, max_date

    # Apply date filter to the full DataFrame. Everything downstream
    # (excluded filter, dashboard view, downloads) works off this.
    date_mask = (df_compiled["date"] >= start_date) & (df_compiled["date"] <= end_date)
    df_full = df_compiled[date_mask].reset_index(drop=True)

    if df_full.empty:
        st.info(
            f"No transactions in selected range "
            f"({start_date.isoformat()} to {end_date.isoformat()})."
        )
        st.stop()

    # Load excluded categories. If categories.txt is missing or invalid,
    # surface the error but don't crash — fall back to no exclusions.
    try:
        _, excluded = load_categories()
    except (FileNotFoundError, ValueError) as e:
        st.warning(f"Could not load category exclusions: {e}")
        excluded = set()

    # df is the dashboard view (excluded categories hidden);
    # df_full is what downloads use (everything within the date range).
    if excluded:
        df = df_full[~df_full["category"].isin(excluded)].reset_index(drop=True)
    else:
        df = df_full

    st.header("Summary")

    total_spend = df["amount"].sum()
    n_tx = len(df)
    n_unmapped = (df["category"] == UNCATEGORISED).sum()
    pct_unmapped = (n_unmapped / n_tx * 100) if n_tx > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total spend", format_sgd(total_spend))
    m2.metric("Transactions", f"{n_tx:,}")
    m3.metric("Unmapped", f"{n_unmapped} ({pct_unmapped:.0f}%)")
    m4.metric(
        "Refunds dropped",
        st.session_state.dropped_negatives,
        help="Counted across the entire upload, not just the selected date range.",
    )

    # Show the active date range so it's always clear what's being summarised.
    is_full_range = (start_date == min_date) and (end_date == max_date)
    if not is_full_range:
        st.caption(
            f"Showing {start_date.isoformat()} to {end_date.isoformat()}. "
            f"Upload covers {min_date.isoformat()} to {max_date.isoformat()}."
        )

    if st.session_state.duplicates_removed > 0:
        st.caption(
            f"Removed {st.session_state.duplicates_removed} duplicate row(s) "
            f"across uploaded files (across all uploaded data)."
        )

    if excluded:
        excluded_in_data = sorted(excluded & set(df_full["category"].unique()))
        if excluded_in_data:
            n_hidden = (df_full["category"].isin(excluded)).sum()
            st.caption(
                f"Hidden from dashboard: {', '.join(excluded_in_data)} "
                f"({n_hidden} transaction{'s' if n_hidden != 1 else ''}). "
                f"Downloads include all categories."
            )

    # --- Spending by category ---
    st.header("Spending by Category")

    cat_summary = (
        df.groupby("category", as_index=False)["amount"]
        .agg(total="sum", count="count")
        .sort_values("total", ascending=False)
        .reset_index(drop=True)
    )

    # Earthy palette — same family as Portfolio Allocation Tool.
    # Rust orange reserved for Uncategorised so it visually stands out.
    PALETTE = [
        "#8B7355",  # taupe
        "#6B8E23",  # olive
        "#8B6F47",  # warm brown
        "#5C7A5C",  # sage
        "#A0826D",  # mocha
        "#7B6F5C",  # stone
        "#9B7E5A",  # camel
        "#6B5D4F",  # bark
        "#A89070",  # sand
        "#5D6B4E",  # forest
    ]

    bar_colors = []
    palette_idx = 0
    for cat in cat_summary["category"]:
        if cat == UNCATEGORISED:
            bar_colors.append("#C77B4F")  # rust — calls attention
        else:
            bar_colors.append(PALETTE[palette_idx % len(PALETTE)])
            palette_idx += 1

    fig = go.Figure(
        go.Bar(
            x=cat_summary["total"],
            y=cat_summary["category"],
            orientation="h",
            marker_color=bar_colors,
            text=[format_sgd(v) for v in cat_summary["total"]],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>%{text}<br>%{customdata} transactions<extra></extra>",
            customdata=cat_summary["count"],
        )
    )
    fig.update_layout(
        height=max(300, 40 * len(cat_summary) + 100),
        margin=dict(l=10, r=80, t=20, b=20),
        xaxis_title="Total spend",
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="Source Sans Pro, sans-serif"),
    )
    fig.update_xaxes(tickprefix="$", tickformat=",.0f", showgrid=True, gridcolor="#EEE")
    st.plotly_chart(fig, use_container_width=True)

    # --- Categorised transactions ---
    st.header("Categorised Transactions")

    fc1, fc2 = st.columns(2)
    with fc1:
        categories_in_data = ["All"] + sorted(df["category"].unique().tolist())
        chosen_cat = st.selectbox("Filter by category", categories_in_data)
    with fc2:
        accounts_in_data = ["All"] + sorted(df["account"].unique().tolist())
        chosen_acct = st.selectbox("Filter by account", accounts_in_data)

    view = df.copy()
    if chosen_cat != "All":
        view = view[view["category"] == chosen_cat]
    if chosen_acct != "All":
        view = view[view["account"] == chosen_acct]

    st.dataframe(
        view[["date", "description", "amount", "category", "account", "matched_pattern"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
            "description": "Description",
            "category": "Category",
            "account": "Account",
            "matched_pattern": "Matched pattern",
        },
    )

    # --- Unmapped section ---
    unmapped_df = df[df["category"] == UNCATEGORISED][
        ["date", "description", "amount", "account"]
    ].reset_index(drop=True)

    with st.expander(f"Unmapped transactions ({len(unmapped_df)})", expanded=False):
        if unmapped_df.empty:
            st.info("Every transaction was mapped. Nice.")
        else:
            st.markdown(
                "These descriptions did not match any pattern in your mapping table. "
                "Use the download below to grow `mapping.xlsx`."
            )
            st.dataframe(
                unmapped_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
                    "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                    "account": "Account",
                },
            )

    # --- Excluded section ---
    excluded_df = df_full[df_full["category"].isin(excluded)][
        ["date", "description", "amount", "category", "account"]
    ].reset_index(drop=True)

    with st.expander(f"Excluded transactions ({len(excluded_df)})", expanded=False):
        if not excluded:
            st.info("No categories are flagged as excluded in `categories.txt`.")
        elif excluded_df.empty:
            st.info(
                "No transactions matched any excluded categories "
                f"({', '.join(sorted(excluded))})."
            )
        else:
            st.markdown(
                "These transactions are hidden from the dashboard view because their "
                "category is flagged with `,exclude` in `categories.txt`. "
                "They are still included in the downloads."
            )
            st.dataframe(
                excluded_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
                    "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                    "description": "Description",
                    "category": "Category",
                    "account": "Account",
                },
            )

    # --- Downloads & history ---
    st.header("Downloads")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    # Downloads use df_full so excluded categories are still in the output —
    # excluded means "hidden from dashboard", not "deleted from data".
    # Account is the friendly label; source_file kept for traceability.
    download_cols = ["date", "description", "amount", "category", "account",
                     "matched_pattern", "source_file"]

    unmapped_full = df_full[df_full["category"] == UNCATEGORISED][
        ["date", "description", "amount"]
    ].reset_index(drop=True)

    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "Download categorised (Excel)",
            data=to_excel_bytes(df_full[download_cols]),
            file_name=f"spending_categorised_{timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
    with d2:
        if st.button(
            "Append unmapped to history",
            type="secondary",
            disabled=unmapped_full.empty,
            help=(
                "Appends new unmapped rows to config/transaction_history.xlsx "
                "(deduped on description). Edit that file in Excel to fill in "
                "categories — they'll be applied on the next Compile."
            ),
        ):
            try:
                n_added, n_skipped = append_to_history(unmapped_full, HISTORY_PATH)
            except (ValueError, PermissionError) as e:
                st.error(f"Could not append to history: {e}")
            else:
                if n_added == 0:
                    st.info(
                        f"Nothing new to add — all {n_skipped} unmapped row(s) "
                        f"are already in `transaction_history.xlsx`."
                    )
                else:
                    msg = f"Appended {n_added} new row(s) to `transaction_history.xlsx`."
                    if n_skipped:
                        msg += f" Skipped {n_skipped} duplicate(s)."
                    msg += " Edit the file in Excel to fill in categories."
                    st.success(msg)

else:
    st.info("Upload one or more statement files and click **Compile** to begin.")
