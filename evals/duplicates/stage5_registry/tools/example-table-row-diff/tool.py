"""Example CSV diff: deterministic, offline, keyed by one column."""

import csv
import io


def diff(left: str, right: str, key: str) -> dict:
    """Return added, removed, and changed keys between two CSV documents."""

    def index(text: str) -> dict[str, list[str]]:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or key not in reader.fieldnames:
            raise ValueError(f"key column {key!r} not present")
        return {row[key]: [row[name] for name in reader.fieldnames] for row in reader}

    a, b = index(left), index(right)
    return {
        "added": sorted(set(b) - set(a)),
        "removed": sorted(set(a) - set(b)),
        "changed": sorted(name for name in set(a) & set(b) if a[name] != b[name]),
    }
