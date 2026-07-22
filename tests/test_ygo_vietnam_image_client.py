from __future__ import annotations

import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PIL import Image

from yugioh_editor.common.card_errors import (
    CardImageNotFoundError,
    CardImageParserError,
    CardImageTransportError,
)
from yugioh_editor.infrastructure.ygo_vietnam_image_client import (
    YgoVietnamImageClient,
)


class _Response:
    def __init__(
        self,
        payload: bytes,
        content_type: str = "",
        *,
        url: str | None = None,
        status: int = 200,
    ) -> None:
        self.payload = payload
        self.headers = Message()
        if content_type:
            self.headers["Content-Type"] = content_type
        self.url = url
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]

    def geturl(self) -> str:
        if self.url is None:
            raise AssertionError("This response requires an explicit final URL.")
        return self.url


class YgoVietnamImageClientTests(unittest.TestCase):
    @staticmethod
    def _png() -> bytes:
        output = BytesIO()
        Image.new("RGB", (320, 460), "blue").save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _page_response(html: bytes, requested_url: str) -> _Response:
        return _Response(html, "text/html; charset=utf-8", url=requested_url)

    @staticmethod
    def _image_response(payload: bytes, image_url: str) -> _Response:
        return _Response(payload, "image/png", url=image_url)

    def test_json_ld_current_cdn_image_is_downloaded_and_name_is_encoded_once(self):
        image_url = "https://cdn.ygovietnam.com/storage/Card/card.png"
        html = (
            b'<script type="application/ld+json">'
            b'{"@type":"CreativeWork","image":"' + image_url.encode() + b'"}</script>'
        )
        page_url = "https://ygovietnam.com/card/Masked%20Beast%20Des%20Gardius"
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(html, page_url),
                self._image_response(self._png(), image_url),
            ],
        ) as opener:
            payload = YgoVietnamImageClient(max_retries=0).fetch_card_image(
                "Masked%20Beast Des Gardius"
            )

        self.assertEqual(payload, self._png())
        self.assertEqual(opener.call_args_list[0].args[0].full_url, page_url)
        self.assertNotIn("%2520", page_url)

    def test_password_image_uses_normalized_cdn_segment_without_leading_zero(self):
        cases = (
            ("08783685", "8783685"),
            ("00001234", "1234"),
            ("12345678", "12345678"),
            (" 0123abcd ", "123ABCD"),
        )
        for password, expected_segment in cases:
            image_url = (
                f"https://cdn.ygovietnam.com/storage/Card/{expected_segment}.jpg"
            )
            with (
                self.subTest(password=password),
                patch(
                    "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                    return_value=self._image_response(self._png(), image_url),
                ) as opener,
            ):
                payload = YgoVietnamImageClient(
                    max_retries=0
                ).fetch_card_image_by_password(password)

            self.assertEqual(payload, self._png())
            self.assertEqual(opener.call_count, 1)
            self.assertEqual(opener.call_args.args[0].full_url, image_url)
            self.assertNotIn("%", opener.call_args.args[0].full_url)

    def test_all_zero_password_uses_single_zero_cdn_segment(self):
        image_url = "https://cdn.ygovietnam.com/storage/Card/0.jpg"
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            return_value=self._image_response(self._png(), image_url),
        ) as opener:
            payload = YgoVietnamImageClient(max_retries=0).fetch_card_image_by_password(
                " 00000000 "
            )

        self.assertEqual(payload, self._png())
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(opener.call_args.args[0].full_url, image_url)

    def test_password_image_rejects_missing_and_invalid_values_without_http(self):
        for password in ("FFFFFFFF", "123", "GGGGGGGG"):
            with (
                self.subTest(password=password),
                patch(
                    "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen"
                ) as opener,
                self.assertRaises(ValueError),
            ):
                YgoVietnamImageClient(max_retries=0).fetch_card_image_by_password(
                    password
                )
            opener.assert_not_called()

    def test_password_image_rejects_redirect_to_known_placeholder_url(self):
        placeholder_url = "https://cdn.ygovietnam.com/storage/Card/placeholder-card.jpg"
        with (
            patch(
                "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                return_value=self._image_response(self._png(), placeholder_url),
            ) as opener,
            self.assertRaisesRegex(
                CardImageTransportError,
                "unapproved image URL",
            ),
        ):
            YgoVietnamImageClient(max_retries=0).fetch_card_image_by_password(
                "01234567"
            )

        self.assertEqual(opener.call_count, 1)
        self.assertEqual(
            opener.call_args.args[0].full_url,
            "https://cdn.ygovietnam.com/storage/Card/1234567.jpg",
        )

    def test_password_image_classifies_404_and_html_200_as_invalid(self):
        image_url = "https://cdn.ygovietnam.com/storage/Card/1234567.jpg"
        with (
            patch(
                "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                side_effect=HTTPError(image_url, 404, "Not Found", {}, None),
            ),
            self.assertRaises(CardImageNotFoundError),
        ):
            YgoVietnamImageClient(max_retries=0).fetch_card_image_by_password(
                "01234567"
            )

        with (
            patch(
                "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                return_value=_Response(
                    b"<html>Error</html>",
                    "image/jpeg",
                    url=image_url,
                ),
            ),
            self.assertRaises(CardImageParserError),
        ):
            YgoVietnamImageClient(max_retries=0).fetch_card_image_by_password(
                "01234567"
            )

        with (
            patch(
                "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                return_value=_Response(
                    b"<html>Error</html>",
                    "text/html; charset=utf-8",
                    url=image_url,
                ),
            ),
            self.assertRaises(CardImageParserError),
        ):
            YgoVietnamImageClient(max_retries=0).fetch_card_image_by_password(
                "01234567"
            )

    def test_page_path_supports_reserved_percent_and_unicode_characters(self):
        image_url = "https://cdn.ygovietnam.com/storage/Card/card.png"
        html = f'<meta property="og:image" content="{image_url}">'.encode()
        names = {
            "Dragon's ‘Mark’ - A&B/C+D#E?F": (
                "Dragon%27s%20%E2%80%98Mark%E2%80%99%20-%20A%26B%2FC%2BD%23E%3FF"
            ),
            "Héros à 100%": "H%C3%A9ros%20%C3%A0%20100%25",
            "Already%20Encoded": "Already%20Encoded",
        }
        for name, encoded_name in names.items():
            with self.subTest(name=name):
                page_url = "https://ygovietnam.com/card/" + encoded_name
                with patch(
                    "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                    side_effect=[
                        self._page_response(html, page_url),
                        self._image_response(self._png(), image_url),
                    ],
                ) as opener:
                    YgoVietnamImageClient(max_retries=0).fetch_card_image(name)
                self.assertEqual(opener.call_args_list[0].args[0].full_url, page_url)

    def test_scoped_main_srcset_beats_logo_and_related_images(self):
        large_url = "https://cdn.ygovietnam.com/storage/Card/main-large.png"
        html = b"".join(
            (
                b'<header><img src="https://cdn.ygovietnam.com/storage/Card/logo.png"></header>',
                b'<section class="related-cards"><img src="https://cdn.ygovietnam.com/storage/Card/other.png"></section>',
                b'<main><div class="card-detail"><img class="card-image" ',
                b'src="https://cdn.ygovietnam.com/storage/Card/main-small.png" ',
                b'srcset="https://cdn.ygovietnam.com/storage/Card/'
                b"main-medium.png 320w, ",
                large_url.encode(),
                b' 960w"></div></main>',
            )
        )
        page_url = "https://ygovietnam.com/card/Card"
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(html, page_url),
                self._image_response(self._png(), large_url),
            ],
        ) as opener:
            YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

        self.assertEqual(opener.call_args_list[1].args[0].full_url, large_url)

    def test_relative_main_image_url_is_resolved_against_final_page_url(self):
        page_url = "https://www.ygovietnam.com/card/Card"
        image_url = "https://www.ygovietnam.com/storage/Card/main.png"
        html = b'<main><img class="card-image" src="/storage/Card/main.png"></main>'
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(html, page_url),
                self._image_response(self._png(), image_url),
            ],
        ) as opener:
            YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

        self.assertEqual(opener.call_args_list[1].args[0].full_url, image_url)

    def test_source_priority_is_scoped_main_then_json_ld_then_open_graph(self):
        main_url = "https://cdn.ygovietnam.com/storage/Card/main.png"
        json_url = "https://cdn.ygovietnam.com/storage/Card/json.png"
        og_url = "https://cdn.ygovietnam.com/storage/Card/open-graph.png"
        html = f"""
            <meta property="og:image" content="{og_url}">
            <script type="application/ld+json">
                {{"@type":"CreativeWork","image":{{"contentUrl":"{json_url}"}}}}
            </script>
            <main><img class="card-image" src="{main_url}"></main>
        """.encode()
        page_url = "https://ygovietnam.com/card/Card"
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(html, page_url),
                self._image_response(self._png(), main_url),
            ],
        ) as opener:
            YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

        self.assertEqual(opener.call_args_list[1].args[0].full_url, main_url)

    def test_json_ld_beats_open_graph_when_no_scoped_main_image_exists(self):
        json_url = "https://cdn.ygovietnam.com/storage/Card/json.png"
        og_url = "https://cdn.ygovietnam.com/storage/Card/open-graph.png"
        html = f"""
            <meta property="og:image" content="{og_url}">
            <script type="application/ld+json">
                [{{"@type":["Thing","CreativeWork"],"image":["{json_url}"]}}]
            </script>
        """.encode()
        page_url = "https://ygovietnam.com/card/Card"
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(html, page_url),
                self._image_response(self._png(), json_url),
            ],
        ) as opener:
            YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

        self.assertEqual(opener.call_args_list[1].args[0].full_url, json_url)

    def test_legacy_image_host_remains_supported(self):
        image_url = "http://ygovietnamcdn.azureedge.net/storage/Card/card.png"
        html = f'<meta property="og:image" content="{image_url}">'.encode()
        page_url = "https://ygovietnam.com/card/Card"
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(html, page_url),
                self._image_response(self._png(), image_url),
            ],
        ):
            payload = YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")
        self.assertEqual(payload, self._png())

    def test_valid_redirects_for_page_and_image_are_accepted(self):
        requested_page_url = "https://ygovietnam.com/card/Card"
        final_page_url = "https://www.ygovietnam.com/card/Card"
        requested_image_url = "https://cdn.ygovietnam.com/storage/Card/card.png"
        final_image_url = "https://cdn.ygovietnam.com/storage/Card/card-final.png"
        html = f'<meta property="og:image" content="{requested_image_url}">'.encode()
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(html, final_page_url),
                self._image_response(self._png(), final_image_url),
            ],
        ) as opener:
            payload = YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

        self.assertEqual(payload, self._png())
        self.assertEqual(opener.call_args_list[0].args[0].full_url, requested_page_url)
        self.assertEqual(opener.call_args_list[1].args[0].full_url, requested_image_url)

    def test_html_error_page_with_http_200_is_not_accepted_as_an_image(self):
        image_url = "https://cdn.ygovietnam.com/storage/Card/card.png"
        html = f'<meta property="og:image" content="{image_url}">'.encode()
        page_url = "https://ygovietnam.com/card/Card"
        with (
            patch(
                "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                side_effect=[
                    self._page_response(html, page_url),
                    _Response(b"<html>Error</html>", "text/html", url=image_url),
                ],
            ),
            self.assertRaises(CardImageParserError),
        ):
            YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

    def test_fake_image_content_type_still_requires_valid_image_bytes(self):
        image_url = "https://cdn.ygovietnam.com/storage/Card/card.png"
        html = f'<meta property="og:image" content="{image_url}">'.encode()
        page_url = "https://ygovietnam.com/card/Card"
        with (
            patch(
                "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                side_effect=[
                    self._page_response(html, page_url),
                    self._image_response(b"<html>Error</html>", image_url),
                ],
            ),
            self.assertRaises(CardImageParserError),
        ):
            YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

    def test_missing_or_unapproved_image_metadata_has_distinct_errors(self):
        page_url = "https://ygovietnam.com/card/Card"
        fixtures = (
            (b"<html><main>No card image</main></html>", CardImageNotFoundError),
            (
                b'<meta property="og:image" content="https://example.com/storage/Card/card.png">',
                CardImageParserError,
            ),
            (
                b'<script type="application/ld+json">{broken</script>',
                CardImageParserError,
            ),
        )
        for html, error_type in fixtures:
            with (
                self.subTest(error_type=error_type.__name__),
                patch(
                    "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                    return_value=self._page_response(html, page_url),
                ),
                self.assertRaises(error_type),
            ):
                YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

    def test_negative_original_query_does_not_block_canonical_query(self):
        japanese_page = "https://ygovietnam.com/card/%E4%BB%AE%E9%9D%A2"
        canonical_page = "https://ygovietnam.com/card/Canonical%20Card"
        image_url = "https://cdn.ygovietnam.com/storage/Card/card.png"
        canonical_html = f'<meta property="og:image" content="{image_url}">'.encode()
        client = YgoVietnamImageClient(max_retries=0)
        with patch(
            "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
            side_effect=[
                self._page_response(b"<html></html>", japanese_page),
                self._page_response(canonical_html, canonical_page),
                self._image_response(self._png(), image_url),
            ],
        ) as opener:
            with self.assertRaises(CardImageNotFoundError):
                client.fetch_card_image("仮面")
            payload = client.fetch_card_image("Canonical Card")

        self.assertEqual(payload, self._png())
        self.assertEqual(opener.call_count, 3)

    def test_http_not_found_and_transport_failures_are_classified(self):
        page_url = "https://ygovietnam.com/card/Card"
        http_not_found = HTTPError(page_url, 404, "Not Found", {}, None)
        failures = (
            (http_not_found, CardImageNotFoundError),
            (URLError("offline"), CardImageTransportError),
        )
        for failure, error_type in failures:
            with (
                self.subTest(error_type=error_type.__name__),
                patch(
                    "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                    side_effect=failure,
                ),
                self.assertRaises(error_type),
            ):
                YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

    def test_unapproved_final_redirect_is_rejected(self):
        requested_url = "https://ygovietnam.com/card/Card"
        with (
            patch(
                "yugioh_editor.infrastructure.ygo_vietnam_image_client.urlopen",
                return_value=self._page_response(
                    b"<html></html>", "https://example.com/card/Card"
                ),
            ) as opener,
            self.assertRaises(CardImageTransportError),
        ):
            YgoVietnamImageClient(max_retries=0).fetch_card_image("Card")

        self.assertEqual(opener.call_args.args[0].full_url, requested_url)


if __name__ == "__main__":
    unittest.main()
