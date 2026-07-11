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

import itertools
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
_SEPARATOR_RE = re.compile(r"[_]+")


def basic_normalize(name) -> str:
    """Uppercase/trim/collapse-whitespace a recipient name; sentinels pass through.

    Underscores are treated as word separators (collapsed to a space) before
    whitespace collapsing, not as meaningful characters — SENTINELS use
    underscores as a plain constant-naming convention (checked separately,
    before this), but in an actual recipient name an underscore is just a
    punctuation artifact of how the bank rendered the same name differently
    across transactions (e.g. "SNOW_CRE" vs "SNOW CRE" for one merchant).
    Left uncollapsed, that single-character difference tanks the fuzzy
    similarity score (token_sort_ratio=50, same as an unrelated pair) and
    the two spellings never merge downstream — collapsing it here fixes the
    root cause instead of tuning a threshold to paper over it.
    """
    if pd.isna(name):
        return "UNKNOWN"
    name = str(name).strip()
    if name in SENTINELS:
        return name
    name = _SEPARATOR_RE.sub(" ", name.upper())
    name = _WHITESPACE_RE.sub(" ", name)
    return name.strip()


def group_by_upi_id(
    df: pd.DataFrame,
    name_col: str = "_basic",
    upi_col: str = "UPI_ID",
    min_name_similarity: int = 55,
    max_distinct_names: int = 5,
) -> dict:
    """Map row index -> canonical name via exact UPI_ID grouping.

    Only covers rows that have a usable (non-null, non-"N/A") UPI_ID.

    Some extracted UPI IDs are truncated payment-gateway/QR session codes
    (e.g. "paytmqr8", "paytm.s1", "bharatpe") rather than a stable
    per-merchant identifier — the same code gets reused across genuinely
    unrelated recipients, sometimes dozens of them. A raw count-majority
    vote doesn't catch this reliably: a single stray row still clears an
    80%-purity bar once the dominant name repeats a handful of times, and
    conversely a genuine 1-vs-1 tie (two rows, same UPI ID, both legitimate
    spelling variants of one payee) has no majority at all and would be
    rejected outright even though it should merge.

    Two checks, verified against real data:

    1. `max_distinct_names` — a real per-payee UPI handle only ever shows a
       handful of spelling variants for one person (observed max: 3 on this
       dataset); a shared gateway code fans out across dozens of unrelated
       payees (observed min: 11, e.g. "paytmqr2" covers 43 distinct names).
       There's a clean gap between those, so a group with more distinct
       names than this is almost certainly a shared code, not a payee
       identifier, and is skipped entirely — its rows fall through to fuzzy
       clustering against the whole dataset instead. This also protects the
       similarity check below: fuzzy-clustering a large, diverse name list
       inevitably produces a few coincidental matches above any reasonable
       threshold just by how many pairs there are to compare.
    2. `min_name_similarity` — for the (now capped, small) remaining
       groups, legitimate shared-UPI rows are near-duplicate spellings of
       the same name (fuzzy score >= ~60 in every case observed, since the
       UPI handle itself is usually name-derived, e.g. "shloksm2" for
       "SHLOK SA"), while a coincidental collision is an unrelated name
       (fuzzy score <= ~50 in every case observed, e.g. "BIKANER" vs
       "SUNIL SH" both under "paytmqr67"). Each group is fuzzy-clustered
       internally (same complete-linkage clustering as the fallback tier
       below, just scoped to this one group) rather than majority-voted, so
       a tied group still merges correctly when the names are genuinely
       similar, and still splits correctly when they aren't.
    """
    mapping = {}
    upi_key = df[upi_col].astype(str).str.strip().str.lower()
    has_upi = df[upi_col].notna() & (upi_key != "n/a") & (upi_key != "")
    for _, group in df[has_upi].groupby(upi_key[has_upi]):
        names = group[name_col]
        names = names[~names.isin(SENTINELS)]
        if names.empty or names.nunique() > max_distinct_names:
            continue
        clusters = cluster_by_fuzzy_name(names.tolist(), threshold=min_name_similarity)
        for idx, name in group[name_col].items():
            if name not in SENTINELS:
                mapping[idx] = clusters[name]
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
    min_upi_name_similarity: int = 55,
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
        work,
        name_col="_basic",
        upi_col=upi_col,
        min_name_similarity=min_upi_name_similarity,
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


def find_prefix_variants(names, min_len: int = 6) -> list:
    """Find canonical-name pairs where one is a literal prefix of the other.

    Both tiers above compare names with length-penalized fuzzy metrics
    (token_sort_ratio/ratio), so a short truncated name scores far below
    any reasonable threshold against its own untruncated form (e.g.
    "PRACHI S" vs "MRS. PRACHI SAMEER SAW" scores 53) and the two never
    merge, even though they're clearly the same payee. A substring-anywhere
    metric like partial_ratio "fixes" that but is far too permissive at
    this string length: short/common fragments ("SON", "ANAS", "DON")
    score 75-100 against dozens of unrelated names, and it even resurrects
    collisions the UPI-similarity gate above was built to reject (e.g.
    "JULFIKAR"/"BIKANER" back at 72.7).

    A literal-prefix check with a minimum length is a much narrower, lower-
    noise middle ground: it only fires when one name starts with the other
    verbatim, which is exactly what bank-statement truncation produces.
    Not merged automatically — some hits are still genuinely ambiguous
    without knowing the account holder's actual contacts (e.g. "KRISHNA"
    is a common enough first name that "KRISHNA"/"KRISHNAM" could be two
    different people) — so this returns candidate pairs for manual review,
    the same way the notebook's multi-variant QA cell does.
    """
    pairs = []
    for a, b in itertools.combinations(sorted(set(names)), 2):
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) >= min_len and short != long_ and long_.startswith(short):
            pairs.append((short, long_))
    return pairs
