"""Singer-name folding shared by the web singer page (sing.py) and the store
(sing_store.py), so both compare names exactly the same way."""

import unicodedata


def fold_name(name):
    """Casefolded, accent-stripped, alnum+space form for name comparison."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = "".join(c if (c.isalnum() or c.isspace()) else " " for c in stripped)
    return " ".join(cleaned.casefold().split())
