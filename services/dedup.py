"""Duplicate candidate detection service."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import Property


def flag_duplicates(properties: list["Property"]) -> int:
    """Flag duplicate candidates among properties.

    Rules (from Tech Spec):
    - Address normalised (remove sub-address below 丁目)
    - Area difference <= 10%
    - Price difference <= 15%
    - Different source sites
    Returns the number of duplicate-candidate pairs flagged.
    """
    count = 0
    n = len(properties)
    for i in range(n):
        for j in range(i + 1, n):
            a = properties[i]
            b = properties[j]

            # Must be from different sources
            if a.sourceName == b.sourceName:
                continue

            # Address similarity (normalise to remove sub-address)
            addr_a = _normalise_address(a.address)
            addr_b = _normalise_address(b.address)
            if addr_a != addr_b or not addr_a:
                continue

            # Area within 10%
            if a.area and b.area:
                diff = abs(a.area - b.area) / max(a.area, b.area)
                if diff > 0.10:
                    continue
            else:
                # If area unknown for either, skip area check
                pass

            # Price within 15%
            if a.price and b.price:
                diff = abs(a.price - b.price) / max(a.price, b.price)
                if diff > 0.15:
                    continue
            else:
                pass

            # Mark both as duplicate candidates
            a.duplicateFlag = True
            b.duplicateFlag = True
            if b.id not in a.duplicateCandidates:
                a.duplicateCandidates.append(b.id)
            if a.id not in b.duplicateCandidates:
                b.duplicateCandidates.append(a.id)
            count += 1

    return count


def _normalise_address(address: str) -> str:
    """Normalise address by removing everything after 丁目/番地."""
    if not address:
        return ""
    # Remove prefecture prefix for comparison
    addr = re.sub(r"^.+?[都道府県]", "", address)
    # Remove everything after the first number sequence (丁目 level and below)
    addr = re.sub(r"\d+丁目.*$", "", addr)
    addr = re.sub(r"\d+-.*$", "", addr)
    addr = re.sub(r"\d+番.*$", "", addr)
    # Remove whitespace
    addr = addr.strip()
    return addr
