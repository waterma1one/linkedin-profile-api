from app.linkedin.normalizer import normalize


def test_resolves_single_reference():
    payload = {
        "data": {"*profile": "urn:li:fsd_profile:1"},
        "included": [{"entityUrn": "urn:li:fsd_profile:1", "firstName": "Ada"}],
    }
    result = normalize(payload)
    assert result.data["profile"]["firstName"] == "Ada"
    assert "*profile" not in result.data


def test_resolves_collection_reference():
    payload = {
        "data": {"**positions": ["urn:li:pos:1", "urn:li:pos:2"]},
        "included": [
            {"entityUrn": "urn:li:pos:1", "title": "Engineer"},
            {"entityUrn": "urn:li:pos:2", "title": "Manager"},
        ],
    }
    result = normalize(payload)
    assert [item["title"] for item in result.data["positions"]] == ["Engineer", "Manager"]


def test_unresolvable_urn_becomes_none_not_an_error():
    payload = {"data": {"*profile": "urn:li:fsd_profile:missing"}, "included": []}
    result = normalize(payload)
    assert result.data["profile"] is None


def test_unresolvable_urns_are_dropped_from_collections():
    payload = {
        "data": {"**positions": ["urn:li:pos:1", "urn:li:pos:missing"]},
        "included": [{"entityUrn": "urn:li:pos:1", "title": "Engineer"}],
    }
    result = normalize(payload)
    assert len(result.data["positions"]) == 1


def test_cycles_do_not_recurse_forever():
    payload = {
        "data": {"*position": "urn:li:pos:1"},
        "included": [
            {"entityUrn": "urn:li:pos:1", "title": "Engineer", "*company": "urn:li:co:1"},
            {"entityUrn": "urn:li:co:1", "name": "Tross", "**positions": ["urn:li:pos:1"]},
        ],
    }
    result = normalize(payload)
    position = result.data["position"]
    assert position["company"]["name"] == "Tross"
    # The back-reference is cut rather than expanded again.
    assert position["company"]["positions"][0] == "urn:li:pos:1"


def test_records_truncation_from_paging_metadata():
    payload = {
        "data": {
            "**elements": ["urn:li:skill:1"],
            "paging": {"start": 0, "count": 1, "total": 47},
            "entityUrn": "urn:li:fsd_profileSkill:section",
        },
        "included": [{"entityUrn": "urn:li:skill:1", "name": "Python"}],
    }
    result = normalize(payload)
    assert result.truncations
    assert result.truncations[0].returned == 1
    assert result.truncations[0].total == 47


def test_no_truncation_when_all_items_returned():
    payload = {
        "data": {
            "**elements": ["urn:li:skill:1"],
            "paging": {"start": 0, "count": 1, "total": 1},
        },
        "included": [{"entityUrn": "urn:li:skill:1", "name": "Python"}],
    }
    assert normalize(payload).truncations == []


def test_depth_cap_stops_runaway_nesting():
    included = [
        {"entityUrn": f"urn:li:n:{i}", "*next": f"urn:li:n:{i + 1}"} for i in range(30)
    ]
    payload = {"data": {"*next": "urn:li:n:0"}, "included": included}
    result = normalize(payload, max_depth=3)
    # Should complete without RecursionError.
    assert result.data["next"] is not None


def test_plain_payload_without_included_is_passed_through():
    payload = {"data": {"firstName": "Ada"}}
    assert normalize(payload).data == {"firstName": "Ada"}
