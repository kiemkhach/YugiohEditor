from __future__ import annotations

import json
import unittest
from email.message import Message
from unittest.mock import patch

from yugioh_editor.infrastructure.yugipedia_alias_client import YugipediaAliasClient


class _Response:
    def __init__(self, value: object) -> None:
        self.payload = json.dumps(value).encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


class YugipediaAliasClientTests(unittest.TestCase):
    def test_explicit_redirect_returns_canonical_title(self):
        response = {
            "query": {
                "redirects": [{"from": "Frog the Jam", "to": "Slime Toad"}],
                "pages": [{"pageid": 1, "title": "Slime Toad"}],
            }
        }
        with patch(
            "yugioh_editor.infrastructure.yugipedia_alias_client.urlopen",
            return_value=_Response(response),
        ) as opener:
            result = YugipediaAliasClient(max_retries=0).resolve_alias("Frog the Jam")
        self.assertEqual(result, "Slime Toad")
        url = opener.call_args.args[0].full_url
        self.assertIn("redirects=1", url)
        self.assertIn("formatversion=2", url)

    def test_missing_title_returns_none_without_guessing(self):
        response = {
            "query": {
                "pages": [{"ns": 0, "title": "Unknown", "missing": True}],
            }
        }
        with patch(
            "yugioh_editor.infrastructure.yugipedia_alias_client.urlopen",
            return_value=_Response(response),
        ):
            self.assertIsNone(
                YugipediaAliasClient(max_retries=0).resolve_alias("Unknown")
            )


if __name__ == "__main__":
    unittest.main()
