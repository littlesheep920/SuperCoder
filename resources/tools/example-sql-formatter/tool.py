"""Example SQL formatter: keyword casing and whitespace only."""

KEYWORDS = {
    "select", "from", "where", "join", "left", "right", "inner", "outer", "on",
    "group", "by", "order", "having", "limit", "insert", "into", "values",
    "update", "set", "delete", "as", "and", "or", "not", "null", "case", "when",
    "then", "else", "end",
}


def format_sql(text: str) -> str:
    """Uppercase keywords and collapse repeated whitespace.

    This is a display aid, not a parser: it does not understand string literals
    or comments and must not be used to rewrite executable SQL.
    """

    words = text.split()
    out = []
    for word in words:
        lowered = word.lower().strip(",()")
        out.append(word.upper() if lowered in KEYWORDS and word.isalpha() else word)
    return " ".join(out)
