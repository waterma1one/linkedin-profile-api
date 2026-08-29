import pytest

from app.errors import InvalidProfileURL
from app.linkedin.urls import parse_profile_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.linkedin.com/in/aditya-singh", "aditya-singh"),
        ("https://www.linkedin.com/in/aditya-singh/", "aditya-singh"),
        ("http://linkedin.com/in/aditya-singh", "aditya-singh"),
        ("www.linkedin.com/in/aditya-singh", "aditya-singh"),
        ("linkedin.com/in/aditya-singh", "aditya-singh"),
        ("https://in.linkedin.com/in/aditya-singh", "aditya-singh"),
        ("https://www.linkedin.com/in/aditya-singh?originalSubdomain=in", "aditya-singh"),
        ("https://www.linkedin.com/in/aditya-singh/en", "aditya-singh"),
        ("https://www.linkedin.com/mwlite/in/aditya-singh", "aditya-singh"),
        ("https://www.linkedin.com/pub/aditya-singh/1/2/3", "aditya-singh"),
        ("  https://www.linkedin.com/in/aditya-singh  ", "aditya-singh"),
        ("https://www.linkedin.com/in/%E5%B1%B1%E7%94%B0", "山田"),
    ],
)
def test_accepts_known_url_forms(raw: str, expected: str) -> None:
    assert parse_profile_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a url",
        "https://example.com/in/aditya-singh",
        "https://www.linkedin.com/company/tross",
        "https://www.linkedin.com/in/",
        "https://linkedin.com.evil.example/in/aditya-singh",
    ],
)
def test_rejects_invalid_urls(raw: str) -> None:
    with pytest.raises(InvalidProfileURL):
        parse_profile_url(raw)
