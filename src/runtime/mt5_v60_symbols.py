from __future__ import annotations


def normalize_mt5_v60_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not cleaned:
        return ""
    for index, char in enumerate(cleaned):
        if not char.isalnum():
            return cleaned[:index]
    return cleaned


def mt5_v60_symbols_match(left: str, right: str) -> bool:
    normalized_left = normalize_mt5_v60_symbol(left)
    normalized_right = normalize_mt5_v60_symbol(right)
    return bool(normalized_left) and normalized_left == normalized_right
