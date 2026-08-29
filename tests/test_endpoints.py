from app.linkedin.endpoints import PROFILE_QUERY_ID, graphql_profile


def test_graphql_url_keeps_restli_argument_syntax_literal():
    url = graphql_profile("ada-lovelace")
    # LinkedIn rejects the request if these are percent-encoded, so assert on the
    # literal characters rather than on a parsed query string.
    assert "variables=(memberIdentity:ada-lovelace)" in url
    assert "%28" not in url and "%3A" not in url


def test_graphql_url_carries_the_query_id_and_web_metadata():
    url = graphql_profile("ada-lovelace")
    assert f"queryId={PROFILE_QUERY_ID}" in url
    assert "includeWebMetadata=true" in url


def test_graphql_url_accepts_a_member_urn_id():
    assert "(memberIdentity:ACoAAAbC123)" in graphql_profile("ACoAAAbC123")
