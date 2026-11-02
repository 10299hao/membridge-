"""Conservative rules for unambiguous adopted decisions; ambiguous text stays reviewable."""

import re


def decision_subject(text, subject=None):
    low = text.casefold()
    if re.search(r"\b(?:not|never|maybe|might|consider|propos\w*|should|could|would|if)\b|\?", low):
        return None
    if not re.search(
        r"\b(?:chose|chosen|decided|decision|agreed|now use|switch\w* to|replace\w* with)\b", low
    ):
        return None
    if subject:
        return subject.strip().casefold()[:100]
    for key, pattern in (
        ("database", r"\b(sqlite|postgres(?:ql)?|mysql|mariadb|mongodb)\b"),
        ("cache", r"\b(redis|memcached)\b"),
        ("test-framework", r"\b(pytest|unittest|jest|vitest)\b"),
        ("default-page-size", r"\bdefault page size\b"),
    ):
        if re.search(pattern, low):
            return key
    return None


def decision_value(text, subject):
    patterns = {
        "database": r"\b(sqlite|postgres(?:ql)?|mysql|mariadb|mongodb)\b",
        "cache": r"\b(redis|memcached)\b",
        "test-framework": r"\b(pytest|unittest|jest|vitest)\b",
        "default-page-size": r"\b\d+\b",
    }
    matches = re.findall(patterns.get(subject, r"(?!)"), text.casefold())
    return matches[0].replace("postgresql", "postgres") if len(matches) == 1 else None
