"""Backward compatibility: existing static/data/properties.json must load
into the new (M1-extended) Property model without ValidationError.

If the file is missing (e.g., fresh checkout), the test is skipped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402

PROPERTIES_JSON = Path(__file__).resolve().parent.parent / "static" / "data" / "properties.json"


@pytest.fixture(scope="module")
def existing_payload():
    if not PROPERTIES_JSON.exists():
        pytest.skip(f"properties.json not present at {PROPERTIES_JSON}")
    with PROPERTIES_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_existing_properties_load_into_new_model(existing_payload):
    """Every entry in production properties.json must instantiate cleanly.

    Extra fields like `isNew` (added downstream of the model) are tolerated
    by pydantic's default ignore policy. 既存 JSON が v2.3/v2.4/v2.5/v2.6 で
    スコア済みでも、現行モデルに読み込めることを確認する（後方互換）。
    """
    props = existing_payload.get("properties", [])
    assert props, "properties.json has no properties; cannot validate compat"

    failures: list[tuple[str, str]] = []
    for raw in props:
        try:
            p = Property(**raw)
        except Exception as e:  # noqa: BLE001
            failures.append((raw.get("id", "<no-id>"), str(e)))
            continue
        # 既存 dealModelVersion はそのまま読み込めるべき (v2.3 / v2.4 / v2.5 / v2.6)
        assert p.dealModelVersion in ("v2.3", "v2.4", "v2.5", "v2.6"), \
            f"unexpected dealModelVersion: {p.dealModelVersion}"
        # フィールドが None で来た場合は None のまま、値があれば値が保たれること
        # (既存スコア済み JSON を破壊しない)

    assert not failures, f"{len(failures)} legacy property records failed validation: {failures[:3]}"


def test_legacy_payload_volume_is_realistic(existing_payload):
    """Sanity guard: at least 100 properties, otherwise compat check is too weak."""
    props = existing_payload.get("properties", [])
    assert len(props) >= 100, f"too few properties ({len(props)}) to be a realistic compat fixture"
