"""T053/F15A: the probe's SigV4 implementation against AWS's published vectors.

The compatibility probe is only meaningful if its own signing is correct: a
verifier that compares our signer against our verifier would accept anything.
These vectors come from AWS's ``aws-sig-v4-test-suite`` (header-based auth) and
the query-string-auth example in the S3 API reference, so the expected signatures
are an external oracle, not a recomputation of our own code.

Sources (fetched 2026-11-04):
* https://github.com/mhart/aws4fetch/tree/master/test/aws-sig-v4-test-suite
  (mirror of the AWS test suite; get-vanilla, get-vanilla-query-order-key-case,
  post-vanilla, post-x-www-form-urlencoded)
* https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html
  (presigned GET example, echoed by the published aws-signature-v4 README)
"""

from __future__ import annotations

import hashlib

from f15a_probe import sigv4

ACCESS_KEY = "AKIDEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
AMZ_DATE = "20150830T123600Z"
REGION = "us-east-1"
SERVICE = "service"
HOST = "example.amazonaws.com"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sign(method: str, path: str, query_pairs, extra_headers, body: bytes) -> str:
    """Sign with the low-level primitives the S3 client also uses.

    The AWS test-suite vectors do not sign ``x-amz-content-sha256`` (they are the
    generic SigV4 suite, not the S3-specific one), while a real S3 request must
    send it. Exercising the same canonicalisation/string-to-sign primitives with
    exactly the vector's signed-header set keeps the oracle external.
    """
    headers = {"host": HOST, "x-amz-date": AMZ_DATE, **extra_headers}
    request, _ = sigv4.canonical_request(
        method=method,
        path=path,
        query=sigv4.canonical_query(query_pairs),
        headers=headers,
        payload_hash=sigv4.sha256_hex(body),
    )
    scope = sigv4.credential_scope(AMZ_DATE, REGION, SERVICE)
    to_sign = sigv4.string_to_sign(AMZ_DATE, scope, request)
    return sigv4.signature(SECRET_KEY, AMZ_DATE[:8], REGION, SERVICE, to_sign)


# ---------------------------------------------------------------------------
# AWS SigV4 test-suite vectors (header-based authentication)
# ---------------------------------------------------------------------------
def test_get_vanilla() -> None:
    signature = _sign("GET", "/", [], {}, b"")
    assert signature == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


def test_get_vanilla_query_order_is_canonicalised() -> None:
    """The request carries Param2 before Param1; the canonical query sorts them."""
    signature = _sign("GET", "/", [("Param2", "value2"), ("Param1", "value1")], {}, b"")
    assert signature == "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"


def test_post_vanilla() -> None:
    signature = _sign("POST", "/", [], {}, b"")
    assert signature == "5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b"


def test_post_x_www_form_urlencoded_hashes_the_body() -> None:
    body = b"Param1=value1"
    signature = _sign(
        "POST",
        "/",
        [],
        {"content-type": "application/x-www-form-urlencoded"},
        body,
    )
    assert sigv4.sha256_hex(body) != EMPTY_SHA256
    assert signature == "ff11897932ad3f4e8b18135d722051e5ac45fc38421b1da7b9d196a0fe09473a"


# ---------------------------------------------------------------------------
# Query-string (presigned URL) vector from the S3 API reference
# ---------------------------------------------------------------------------
def test_presigned_get_matches_the_s3_api_reference_example() -> None:
    url = sigv4.presign_url(
        scheme="https",
        host="examplebucket.s3.amazonaws.com",
        path="/test.txt",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        service="s3",
        amz_date="20130524T000000Z",
        expires=86400,
    )
    assert url.startswith(
        "https://examplebucket.s3.amazonaws.com/test.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    )
    # The query parameters are sorted: Algorithm, Credential, Date, Expires, SignedHeaders.
    assert url.endswith(
        "X-Amz-SignedHeaders=host&X-Amz-Signature="
        "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
    )


def test_uri_encoding_double_encodes_nothing_and_keeps_slashes_in_paths() -> None:
    assert sigv4.uri_encode("logs/my-file_1.txt") == "logs%2Fmy-file_1.txt"
    assert sigv4.uri_encode("a b+c", encode_slash=False) == "a%20b%2Bc"
    assert sigv4.canonical_uri("/bucket/tenant/../sealed/x") == "/bucket/tenant/../sealed/x"


def test_signed_header_names_are_sorted_and_lowercase() -> None:
    signed = sigv4.sign_headers(
        method="PUT",
        host=HOST,
        path="/bucket/key",
        query_pairs=[],
        extra_headers={"Content-Type": "text/plain", "X-Amz-Meta-Probe": "1"},
        payload=b"abc",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region=REGION,
        service=SERVICE,
        amz_date=AMZ_DATE,
    )
    assert signed.signed_headers == (
        "content-type;host;x-amz-content-sha256;x-amz-date;x-amz-meta-probe"
    )
    lines = signed.canonical_request.split("\n")
    assert lines[0] == "PUT"
    assert "x-amz-content-sha256:" + sigv4.sha256_hex(b"abc") in lines


def test_a_different_secret_produces_a_different_signature() -> None:
    def sign(secret: str) -> str:
        request, _ = sigv4.canonical_request(
            method="GET",
            path="/",
            query="",
            headers={"host": HOST, "x-amz-date": AMZ_DATE},
            payload_hash=sigv4.sha256_hex(b""),
        )
        scope = sigv4.credential_scope(AMZ_DATE, REGION, SERVICE)
        to_sign = sigv4.string_to_sign(AMZ_DATE, scope, request)
        return sigv4.signature(secret, AMZ_DATE[:8], REGION, SERVICE, to_sign)

    expected = "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    assert sign(SECRET_KEY) == expected
    assert sign("another-secret") != expected
