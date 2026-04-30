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

# --- Session state ---
for key, default in [
    ("compiled", None),
    ("dropped_negatives", 0),
    ("duplicates_removed", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# --- Helpers ---
def compile_statements(uploaded_files, parser_func) -> pd.DataFrame:
    """Parse all uploaded files, concatenate, dedupe, drop negatives, categorise."""
    frames = []
    for uf in uploaded_files:
        try:
            frames.append(parser_func(uf.getvalue(), uf.name))
        except ValueError as e:
            st.error(f"Failed to parse '{uf.name}': {e}")
            return None

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True)

    # Dedupe on (date, amount, description)
    before = len(df)
    df = df.drop_duplicates(subset=["date", "amount", "description"]).reset_index(drop=True)
    st.session_state.duplicates_removed = before - len(df)

    # Drop refunds / payments
    before = len(df)
    df = df[df["amount"] > 0].reset_index(drop=True)
    st.session_state.dropped_negatives = before - len(df)

    # Categorise
    try:
        mapping = load_mapping(MAPPING_PATH)
    except FileNotFoundError as e:
        st.error(str(e))
        return None

    df = categorise_dataframe(df, mapping)
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

col_format, col_files = st.columns([1, 3])
with col_format:
    chosen_format = st.selectbox("Statement format", list(PARSERS.keys()))
with col_files:
    uploaded = st.file_uploader(
        "Drop one or more statement files",
        type=["xlsx", "csv"],
        accept_multiple_files=True,
    )

if st.button("Compile", type="primary", disabled=not uploaded):
    with st.spinner("Parsing and categorising..."):
        result = compile_statements(uploaded, PARSERS[chosen_format])
        if result is not None:
            st.session_state.compiled = result

# --- Results section ---
if st.session_state.compiled is not None:
    df_full = st.session_state.compiled

    # Load excluded categories. If categories.txt is missing or invalid,
    # surface the error but don't crash — fall back to no exclusions.
    try:
        _, excluded = load_categories()
    except (FileNotFoundError, ValueError) as e:
        st.warning(f"Could not load category exclusions: {e}")
        excluded = set()

    # df is the dashboard view (excluded categories hidden);
    # df_full is what downloads use (everything).
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
    m4.metric("Refunds dropped", st.session_state.dropped_negatives)

    if st.session_state.duplicates_removed > 0:
        st.caption(f"Removed {st.session_state.duplicates_removed} duplicate row(s) across uploaded files.")

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

    categories_in_data = ["All"] + sorted(df["category"].unique().tolist())
    chosen_cat = st.selectbox("Filter by category", categories_in_data)

    view = df if chosen_cat == "All" else df[df["category"] == chosen_cat]

    st.dataframe(
        view[["date", "description", "amount", "category", "matched_pattern", "source_file"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
            "description": "Description",
            "category": "Category",
            "matched_pattern": "Matched pattern",
            "source_file": "Source file",
        },
    )

    # --- Unmapped section ---
    unmapped_df = df[df["category"] == UNCATEGORISED][
        ["date", "description", "amount", "source_file"]
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
                },
            )

    # --- Excluded section ---
    excluded_df = df_full[df_full["category"].isin(excluded)][
        ["date", "description", "amount", "category", "source_file"]
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
                    "source_file": "Source file",
                },
            )

    # --- Downloads ---
    st.header("Downloads")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    # Downloads use df_full so excluded categories are still in the output —
    # excluded means "hidden from dashboard", not "deleted from data".
    unmapped_full = df_full[df_full["category"] == UNCATEGORISED][
        ["date", "description", "amount", "source_file"]
    ].reset_index(drop=True)

    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "Download categorised (Excel)",
            data=to_excel_bytes(df_full[["date", "description", "amount", "category", "matched_pattern", "source_file"]]),
            file_name=f"spending_categorised_{timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
    with d2:
        st.download_button(
            "Download unmapped (Excel)",
            data=to_excel_bytes(unmapped_full),
            file_name=f"spending_unmapped_{timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary",
            disabled=unmapped_full.empty,
        )

else:
    st.info("Upload one or more statement files and click **Compile** to begin.")
