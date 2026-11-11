import pytest

from app.parser import ParseError, detect_columns, generate_password, generate_username, parse_ticket_html


def test_parses_sample_ticket(sample_ticket):
    users, skipped = parse_ticket_html(sample_ticket["description"])
    assert [u.username for u in users] == ["abebe.kebede", "sara.tesfaye"]
    abebe = users[0]
    assert abebe.email == "abebe.kebede@example.com"  # taken from the mailto: link
    assert abebe.groups == ["Finance-RW", "VPN-Users"]
    assert abebe.ou == "OU=Finance,OU=Users,DC=corp,DC=example,DC=com"  # nested table unwrapped
    assert users[1].display_name == "Sara Tesfaye"  # defaulted from first + last name
    assert len(skipped) == 1 and "LastName" in skipped[0].reason


def test_exact_header_match_beats_substring():
    cols = detect_columns(["Groups", "OU", "FirstName", "LastName"])
    assert cols["ou"] == 1 and cols["groups"] == 0


def test_duplicate_usernames_are_skipped():
    row = "<tr><td>Abel</td><td>Haile</td><td>OU=X,DC=example,DC=com</td></tr>"
    html = f"<table><tr><td>FirstName</td><td>LastName</td><td>OU</td></tr>{row}{row}</table>"
    users, skipped = parse_ticket_html(html)
    assert len(users) == 1 and "Duplicate" in skipped[0].reason


@pytest.mark.parametrize(
    "html, message",
    [
        ("", "empty"),
        ("<p>no table here</p>", "No table"),
        ("<table><tr><td>FirstName</td></tr></table>", "no data rows"),
        ("<table><tr><td>Name</td></tr><tr><td>x</td></tr></table>", "Required columns"),
    ],
)
def test_bad_input_raises(html, message):
    with pytest.raises(ParseError, match=message):
        parse_ticket_html(html)


def test_username_is_clean_and_ad_length():
    assert generate_username("Ab'ebe", "Kebede-Long-Surname") == "abebe.kebedelongsurn"
    assert len(generate_username("a" * 30, "b" * 30)) == 20


def test_password_meets_complexity():
    for _ in range(50):
        pw = generate_password()
        assert len(pw) == 16
        assert any(c.isupper() for c in pw) and any(c.islower() for c in pw) and any(c.isdigit() for c in pw)
