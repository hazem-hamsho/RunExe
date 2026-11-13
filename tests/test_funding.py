from urllib.parse import parse_qs, urlparse

import pytest

from runexe.frontend.windows.funding import build_paypal_donation_url


def test_paypal_donation_url_contains_selected_amount():
    url = urlparse(build_paypal_donation_url(20))
    query = parse_qs(url.query)

    assert url.scheme == "https"
    assert url.netloc == "www.paypal.com"
    assert url.path == "/donate"
    assert query["amount"] == ["20"]
    assert query["currency_code"] == ["USD"]
    assert query["item_name"] == ["RunExe"]
    assert query["return"] == [
        "https://github.com/hazem-hamsho/RunExe/releases"
    ]


def test_paypal_donation_url_rejects_amount_below_minimum():
    with pytest.raises(ValueError):
        build_paypal_donation_url(2)
