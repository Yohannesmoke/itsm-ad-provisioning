"""Parse the user table that requesters paste into an ITSM ticket description.

ManageEngine ServiceDesk Plus (and most ITSM tools) store the ticket description
as rich-text HTML. Requesters fill in a table like:

    FirstName | LastName | DisplayName | Description | Telephone | Email | Manager | Groups | OU

Groups are separated by semicolons. Some editors wrap a cell value in a nested
<table>, so only direct child cells are read. Usernames and passwords are
generated when the requester does not supply them.
"""

from __future__ import annotations

import logging
import re
import secrets
import string
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

COLUMN_ALIASES: dict[str, list[str]] = {
    "first_name": ["firstname", "givenname"],
    "last_name": ["lastname", "surname"],
    "display_name": ["displayname"],
    "description": ["description", "jobtitle"],
    "phone": ["telephonenumber", "telephone", "phone", "mobile"],
    "email": ["email", "e-mail", "mail"],
    "manager": ["manager"],
    "groups": ["groups", "usergroups", "group"],
    "ou": ["ou", "organizationalunit", "path"],
    "username": ["username", "userlogonname", "samaccountname"],
}

REQUIRED = {"first_name": "FirstName", "last_name": "LastName", "ou": "OU"}
PASSWORD_SPECIALS = "!@#$%^&*-_"  # noqa: S105 - character set, not a secret


class ParseError(ValueError):
    """The ticket description could not be turned into user records."""


@dataclass
class UserRequest:
    first_name: str
    last_name: str
    ou: str
    username: str
    password: str = field(repr=False)
    display_name: str = ""
    description: str = ""
    phone: str = ""
    email: str = ""
    manager: str = ""
    groups: list[str] = field(default_factory=list)
    change_password_at_logon: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SkippedRow:
    row: int
    reason: str
    name: str = ""


def _normalise(text: str) -> str:
    return re.sub(r"[\s_]+", "", text).lower()


def _cell_text(cell: Tag) -> str:
    text = cell.get_text(separator=" ", strip=True).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _cell_email(cell: Tag) -> str:
    link = cell.find("a", href=True)
    if link and link["href"].lower().startswith("mailto:"):
        return link["href"][7:].split("?")[0].strip()
    return _cell_text(cell)


def detect_columns(headers: list[str]) -> dict[str, int]:
    """Map internal field names to column indexes.

    Exact matches win over substring matches so that, for example, "OU" is not
    matched inside "Groups".
    """
    normalised = [_normalise(h) for h in headers]
    mapping: dict[str, int] = {}
    used: set[int] = set()
    for exact in (True, False):
        for key, aliases in COLUMN_ALIASES.items():
            if key in mapping:
                continue
            for alias in aliases:
                idx = next(
                    (i for i, h in enumerate(normalised) if i not in used and (h == alias if exact else alias in h)),
                    None,
                )
                if idx is not None:
                    mapping[key] = idx
                    used.add(idx)
                    break
    return mapping


def generate_username(first: str, last: str) -> str:
    clean = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    return f"{clean(first)}.{clean(last)}"[:20]  # sAMAccountName max length is 20


def generate_password(length: int = 16) -> str:
    """Random password that satisfies the default AD complexity policy."""
    alphabet = string.ascii_letters + string.digits + PASSWORD_SPECIALS
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in PASSWORD_SPECIALS for c in pw)
        ):
            return pw


def _value(cells: list[Tag], idx: int | None, key: str) -> str:
    if idx is None or idx >= len(cells):
        return ""
    return _cell_email(cells[idx]) if key == "email" else _cell_text(cells[idx])


def parse_ticket_html(html: str) -> tuple[list[UserRequest], list[SkippedRow]]:
    if not html or not html.strip():
        raise ParseError("Ticket description is empty")

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="ze_tableView") or soup.find("table")
    if table is None:
        raise ParseError("No table found in ticket description")

    body = table.find("tbody", recursive=False) or table
    rows = body.find_all("tr", recursive=False)
    if len(rows) < 2:
        raise ParseError("Table has no data rows")

    headers = [_cell_text(c) for c in rows[0].find_all(["th", "td"], recursive=False)]
    columns = detect_columns(headers)
    missing_cols = [label for key, label in REQUIRED.items() if key not in columns]
    if missing_cols:
        raise ParseError(f"Required columns not found: {', '.join(missing_cols)} (headers: {headers})")

    users: list[UserRequest] = []
    skipped: list[SkippedRow] = []
    seen: set[str] = set()

    for row_no, row in enumerate(rows[1:], start=1):
        cells = row.find_all(["td", "th"], recursive=False)
        values = {key: _value(cells, columns.get(key), key) for key in COLUMN_ALIASES}
        if not any(values.values()):
            continue

        name = f"{values['first_name']} {values['last_name']}".strip()
        missing = [label for key, label in REQUIRED.items() if not values[key]]
        if missing:
            skipped.append(SkippedRow(row_no, f"Missing required fields: {', '.join(missing)}", name))
            continue

        username = values["username"] or generate_username(values["first_name"], values["last_name"])
        if username in seen:
            skipped.append(SkippedRow(row_no, f"Duplicate username in ticket: {username}", name))
            continue
        seen.add(username)

        users.append(
            UserRequest(
                first_name=values["first_name"],
                last_name=values["last_name"],
                ou=values["ou"],
                username=username,
                password=generate_password(),
                display_name=values["display_name"] or name,
                description=values["description"],
                phone=values["phone"],
                email=values["email"],
                manager=values["manager"],
                groups=[g.strip() for g in values["groups"].split(";") if g.strip()],
            )
        )

    logger.info("Parsed %d user(s), skipped %d row(s)", len(users), len(skipped))
    return users, skipped
