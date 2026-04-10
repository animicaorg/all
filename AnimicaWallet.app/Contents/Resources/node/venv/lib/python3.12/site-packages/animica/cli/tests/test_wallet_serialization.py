from pathlib import Path

import pytest

from animica.wallet.serialization import (
    WalletParseError,
    export_canonical_store,
    merge_imported_wallets,
    parse_wallets_text,
)


FIXTURES = Path('tests/fixtures/wallets')


def test_parse_legacy_map_to_canonical() -> None:
    result = parse_wallets_text((FIXTURES / 'legacy_map.json').read_text())
    assert result.store['format'] == 'animica.wallets'
    assert result.store['version'] == 2
    assert result.store['wallets'][0]['label'] == 'alice'


def test_parse_legacy_array_to_canonical() -> None:
    result = parse_wallets_text((FIXTURES / 'legacy_array.json').read_text())
    assert result.store['wallets'][0]['label'] == 'bob'
    assert result.store['wallets'][0]['alg_id'] == 4098


def test_export_import_roundtrip_canonical() -> None:
    original = parse_wallets_text((FIXTURES / 'canonical_v2.json').read_text()).store
    exported = export_canonical_store(original)
    reparsed = parse_wallets_text(__import__('json').dumps(exported)).store
    assert reparsed['format'] == 'animica.wallets'
    assert reparsed['wallets'][0]['address'] == original['wallets'][0]['address']


def test_merge_collision_renames_labels() -> None:
    existing = parse_wallets_text((FIXTURES / 'canonical_v2.json').read_text()).store
    imported = parse_wallets_text((FIXTURES / 'canonical_v2.json').read_text()).store
    merged = merge_imported_wallets(existing, imported, mode='merge')
    labels = [w['label'] for w in merged['wallets']]
    assert 'alice' in labels
    assert 'alice-2' in labels


def test_parse_preserves_pending_txs_runtime_metadata() -> None:
    result = parse_wallets_text(
        """
        {
          "wallets": [
            {
              "label": "alice",
              "address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
              "alg_id": 4097,
              "public_key_hex": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "secret_key_hex": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              "created_at": "2024-01-01T00:00:00Z",
              "pending_txs": [
                {
                  "tx_hash": "0xabc",
                  "status": "mempool_accepted",
                  "reserve_amount": 11
                }
              ]
            }
          ]
        }
        """
    )
    assert result.store["wallets"][0]["pending_txs"][0]["reserve_amount"] == 11


def test_invalid_json_line_col() -> None:
    with pytest.raises(WalletParseError) as exc:
        parse_wallets_text('{"wallets": [}', source='bad.json')
    assert 'line' in str(exc.value)
    assert 'column' in str(exc.value)


def test_invalid_hex_fails() -> None:
    bad = '[{"label":"x","address":"anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqz6j8g5","alg_id":4097,"public_key_hex":"zz"}]'
    result = parse_wallets_text(bad)
    assert result.failures
