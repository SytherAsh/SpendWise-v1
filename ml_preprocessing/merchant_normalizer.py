"""Bank-agnostic merchant/recipient name normalization.

Two-tier canonicalization for a `Recipient_Name` column produced by any
bank-specific statement parser (e.g. `Segregation.ipynb` for SBI):

1. Exact `UPI_ID` grouping (primary) — rows sharing a UPI ID are
   unambiguously the same payee, regardless of how the name was spelled
   on a given transaction.
2. Fuzzy name clustering (fallback) — for rows without a usable UPI ID,
   cluster near-duplicate name spellings with rapidfuzz similarity +
   networkx connected components.

Kept independent of any bank-specific parsing so it can be reused once
other banks are supported.
"""

import re
from collections import Counter

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

SENTINELS = {
    "UNKNOWN",
    "PHONE_TRANSFER",
    "ATM_WITHDRAWAL",
    "BANK_FEE",
    "INTEREST_CREDIT",
    "INSURANCE_PREMIUM",
    "CHEQUE_CLEARING",
    "REVERSAL",
}
_WHITESPACE_RE = re.compile(r"\s+")


def basic_normalize(name) -> str:
    """Uppercase/trim/collapse-whitespace a recipient name; sentinels pass through."""
    if pd.isna(name):
        return "UNKNOWN"
    name = str(name).strip()
    if name in SENTINELS:
        return name
    name = _WHITESPACE_RE.sub(" ", name.upper())
    return name.strip()


def group_by_upi_id(
    df: pd.DataFrame,
    name_col: str = "_basic",
    upi_col: str = "UPI_ID",
    min_purity: float = 0.8,
) -> dict:
    """Map row index -> canonical name via exact UPI_ID grouping.

    Only covers rows that have a usable (non-null, non-"N/A") UPI_ID.
    Canonical name per UPI ID = the most frequent name variant seen for it.

    Some extracted UPI IDs are truncated payment-gateway codes (e.g. a
    Paytm QR prefix like "paytmqr2", or "bharatpe") rather than a real
    per-merchant identifier — the same truncated code shows up under several
    genuinely different recipient names, each roughly equally often. A raw
    "at most N distinct names" cutoff isn't enough to catch this (three
    unrelated names appearing once each still looks small); instead a UPI
    ID is only trusted when one name variant clearly dominates its rows
    (>= `min_purity` share). Groups below that purity are skipped — those
    rows fall through to fuzzy name clustering instead.
    """
    mapping = {}
    upi_key = df[upi_col].astype(str).str.strip().str.lower()
    has_upi = df[upi_col].notna() & (upi_key != "n/a") & (upi_key != "")
    for _, group in df[has_upi].groupby(upi_key[has_upi]):
        names = group[name_col]
        names = names[~names.isin(SENTINELS)]
        if names.empty:
            continue
        counts = names.value_counts()
        top_name, top_count = counts.index[0], counts.iloc[0]
        if top_count / len(names) < min_purity:
            continue
        for idx, name in group[name_col].items():
            if name not in SENTINELS:
                mapping[idx] = top_name
    return mapping


def cluster_by_fuzzy_name(names: list, threshold: int = 90) -> dict:
    """Cluster a list of normalized names by fuzzy similarity (complete linkage).

    Complete linkage requires every pair within a cluster to meet `threshold`,
    which avoids the single-linkage "chaining" failure mode (A~B~C merged
    transitively even though A and C are unrelated) that connected-components
    clustering is prone to, especially with short truncated names.

    Returns {name: canonical_name}; canonical = most frequent variant
    among `names` (ties broken by shorter string).
    """
    unique_names = list(dict.fromkeys(names))
    counts = Counter(names)

    if len(unique_names) <= 1:
        return {name: name for name in unique_names}

    n = len(unique_names)
    distance = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            score = fuzz.token_sort_ratio(unique_names[i], unique_names[j])
            distance[i, j] = distance[j, i] = 100 - score

    condensed = squareform(distance, checks=False)
    linkage_matrix = linkage(condensed, method="complete")
    labels = fcluster(linkage_matrix, t=100 - threshold, criterion="distance")

    clusters: dict = {}
    for name, label in zip(unique_names, labels):
        clusters.setdefault(label, []).append(name)

    mapping = {}
    for members in clusters.values():
        canonical = max(members, key=lambda nm: (counts[nm], -len(nm)))
        for name in members:
            mapping[name] = canonical
    return mapping


def normalize_recipients(
    df: pd.DataFrame,
    threshold: int = 90,
    name_col: str = "Recipient_Name",
    upi_col: str = "UPI_ID",
    min_upi_purity: float = 0.8,
) -> pd.Series:
    """Top-level entry point: canonical recipient names aligned to df.index.

    Combines exact UPI_ID grouping (tier 1) with fuzzy name clustering
    among the leftover rows (tier 2). `UNKNOWN`/`PHONE_TRANSFER` sentinels
    are left untouched.
    """
    basic = df[name_col].apply(basic_normalize)
    work = df.assign(_basic=basic)

    canonical = basic.copy()

    upi_mapping = group_by_upi_id(
        work, name_col="_basic", upi_col=upi_col, min_purity=min_upi_purity
    )
    for idx, name in upi_mapping.items():
        canonical.loc[idx] = name

    covered = set(upi_mapping.keys())
    remaining_mask = ~basic.index.isin(covered) & ~basic.isin(SENTINELS)
    remaining_names = basic[remaining_mask].tolist()
    if remaining_names:
        fuzzy_mapping = cluster_by_fuzzy_name(remaining_names, threshold=threshold)
        canonical.loc[remaining_mask] = basic[remaining_mask].map(fuzzy_mapping)

    return canonical
