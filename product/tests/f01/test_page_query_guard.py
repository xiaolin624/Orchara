"""Regression tests for the page query-parameter guard (fail closed).

The previous guard matched only ``URLSearchParams(...).get('x')`` with single
quotes. A double-quoted read, a ``new URL(...).searchParams`` read, or a key held
in a variable extracted zero keys and therefore passed. These tests pin the new
fail-closed behaviour: the guard must reject a read whose key it cannot prove,
and must still allow the three non-secret parameters the pages really use.
"""

from __future__ import annotations

import pytest

from conftest import assert_no_token_in_url_or_storage, query_parameter_keys


def test_single_quoted_get_is_extracted_and_allowed() -> None:
    body = "const runID = new URLSearchParams(window.location.search).get('run');"
    assert query_parameter_keys(body) == ({"run"}, False)
    assert_no_token_in_url_or_storage(body, "runs.html")


def test_double_quoted_get_is_extracted_and_allowed() -> None:
    body = 'const reason = new URLSearchParams(window.location.search).get("error");'
    assert query_parameter_keys(body) == ({"error"}, False)
    assert_no_token_in_url_or_storage(body, "login.html")


def test_search_params_get_is_extracted_and_allowed() -> None:
    body = 'const version = new URL("https://example.test/x").searchParams.get("version");'
    assert query_parameter_keys(body) == ({"version"}, False)
    assert_no_token_in_url_or_storage(body, "chat.html")


def test_a_double_quoted_unrecognised_key_fails() -> None:
    body = 'new URLSearchParams(window.location.search).get("token")'
    with pytest.raises(AssertionError, match="unexpected query parameters"):
        assert_no_token_in_url_or_storage(body, "page.html")


def test_a_search_params_style_token_fails() -> None:
    body = 'new URL("https://example.test/x").searchParams.get("csrf")'
    with pytest.raises(AssertionError, match="unexpected query parameters"):
        assert_no_token_in_url_or_storage(body, "page.html")


def test_a_key_held_in_a_variable_fails_closed() -> None:
    body = (
        "const key = 'token';\n"
        "const value = new URLSearchParams(window.location.search).get(key);\n"
    )
    assert query_parameter_keys(body) == (set(), True)
    with pytest.raises(AssertionError, match="could not extract its key"):
        assert_no_token_in_url_or_storage(body, "page.html")


def test_an_unrecognised_urlsearchparams_argument_fails_closed() -> None:
    body = "const query = new URLSearchParams(location.hash).get('run');"
    assert query_parameter_keys(body) == ({"run"}, True)
    with pytest.raises(AssertionError, match="could not extract its key"):
        assert_no_token_in_url_or_storage(body, "page.html")


def test_a_local_query_builder_does_not_read_the_address_bar() -> None:
    body = (
        "const query = new URLSearchParams();\n"
        "query.set('limit', '50');\n"
        "query.set('cursor', cursor);\n"
    )
    assert query_parameter_keys(body) == (set(), False)
    assert_no_token_in_url_or_storage(body, "models.html")


def test_storage_and_url_token_bans_still_apply() -> None:
    with pytest.raises(AssertionError, match="localStorage"):
        assert_no_token_in_url_or_storage("localStorage.setItem('x', y)", "page.html")
    with pytest.raises(AssertionError, match="puts a token in a URL"):
        assert_no_token_in_url_or_storage("fetch('/x?token=' + token)", "page.html")
    with pytest.raises(AssertionError, match="puts a CSRF token in a URL"):
        assert_no_token_in_url_or_storage("fetch('/x?csrf=' + csrf)", "page.html")
