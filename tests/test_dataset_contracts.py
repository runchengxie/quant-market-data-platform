import pytest

from quant_market_data_platform.dataset_contracts import DatasetContract, validate_dataset_contract


def test_dataset_contract_payload_preserves_data_semantics() -> None:
    contract = DatasetContract(
        dataset_id="cn_a_share_l2_order",
        provider="vendor_x",
        market="a_share",
        asset_schema_version="vendor_x.order.v3",
        data_version="20260830",
        primary_key=("trading_day", "symbol", "channel", "sequence"),
        time={"timezone": "Asia/Shanghai", "event_time_semantics": "exchange_generated"},
        units={"price": "fen", "volume": "shares"},
        null_semantics={"Price": {"nullable": True}},
        sentinels={"Price": [{"raw_value": -999, "meaning": "protocol_no_value"}]},
        ordering={
            "exchange_sequence_available": True,
            "channel_column": "ChannelNo",
            "sequence_column": "ApplSeqNum",
            "cross_channel_total_order": False,
        },
        expected_cadence={"type": "trading_day"},
        quality_rules={"exchange_sequence_gaps": "research_only"},
        pit={"applicable": False},
    )

    payload = contract.to_payload()

    assert payload["schema_version"] == "market_data_platform.dataset_contract.v1"
    assert payload["dataset"]["id"] == "cn_a_share_l2_order"
    assert payload["primary_key"] == ["trading_day", "symbol", "channel", "sequence"]
    assert payload["ordering"]["sequence_column"] == "ApplSeqNum"
    assert payload["quality_rules"] == {"exchange_sequence_gaps": "research_only"}
    assert validate_dataset_contract(payload) == payload


def test_dataset_contract_rejects_sequence_claim_without_sequence_column() -> None:
    contract = DatasetContract(
        dataset_id="bad",
        provider="vendor_x",
        market="a_share",
        asset_schema_version="v1",
        ordering={"exchange_sequence_available": True},
    )

    with pytest.raises(ValueError, match="sequence_column"):
        contract.to_payload()


def test_dataset_contract_carries_quality_rule_overrides() -> None:
    contract = DatasetContract(
        dataset_id="cn_a_share_l2_snapshot",
        provider="vendor_x",
        market="a_share",
        asset_schema_version="v1",
        quality_rules={"null_values": "ignore", "exchange_sequence_gaps": "quarantine"},
    )

    assert contract.to_payload()["quality_rules"] == {
        "null_values": "ignore",
        "exchange_sequence_gaps": "quarantine",
    }


def _key_payload(primary_key):
    return {
        "schema_version": "market_data_platform.dataset_contract.v1",
        "dataset": {
            "id": "orders",
            "provider": "vendor_x",
            "market": "a_share",
            "schema_version": "v1",
        },
        "primary_key": primary_key,
    }


@pytest.mark.parametrize("key", [None, 17, True, {}, [], ["nested"], "", " \t"])
def test_primary_key_entries_are_nonempty_strings(key):
    with pytest.raises(ValueError, match="primary_key must be a list of non-empty strings"):
        validate_dataset_contract(_key_payload(["symbol", key]))


@pytest.mark.parametrize("existing", [False, True])
def test_invalid_primary_key_never_creates_or_replaces_contract(tmp_path, existing):
    from quant_market_data_platform.dataset_contracts import write_dataset_contract

    output = tmp_path / "contracts" / "orders.json"
    if existing:
        output.parent.mkdir()
        output.write_text("existing contract must survive\n")
    with pytest.raises(ValueError, match="primary_key"):
        write_dataset_contract(output, _key_payload([None]))
    if existing:
        assert output.read_text() == "existing contract must survive\n"
    else:
        assert not output.parent.exists()


@pytest.mark.parametrize("keys", [[], ["symbol", "时间"], [" column ", "123"]])
def test_valid_primary_keys_roundtrip_without_coercion(tmp_path, keys):
    import json

    from quant_market_data_platform.dataset_contracts import write_dataset_contract

    payload = _key_payload(keys)
    output = tmp_path / "contracts" / "orders.json"
    assert validate_dataset_contract(payload) == payload
    write_dataset_contract(output, payload)
    assert json.loads(output.read_text()) == payload


def test_duplicate_primary_key_names_remain_invalid():
    with pytest.raises(ValueError, match="primary_key entries must be unique"):
        validate_dataset_contract(_key_payload(["symbol", "symbol"]))
