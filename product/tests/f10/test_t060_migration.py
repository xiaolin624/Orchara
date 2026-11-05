"""F10/T060 migration acceptance: the admission vocabulary admits the pdf format.

``platform.artifact_admissions.detected_format`` was a closed vocabulary until
0015. The PDF sniff produces ``detected_format = 'pdf'``, so the finalize handler
could not persist an accepted admission without 0038 (the real stack test proves
that write). This test pins the migration name, the chain continuity and the new
closed vocabulary, plus the unchanged worker zero-grant rule.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import migrated_database  # noqa: F401 - pytest fixture


def test_the_migration_chain_is_contiguous_and_0038_extends_the_format_vocabulary(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    names = st.migration_chain(dbname)
    assert names["0038"] == "0038_pdf_admission.sql"
    for offset, version in enumerate(sorted(names)):
        assert version == f"{offset + 1:04d}", (version, names)

    definition = st.sql(
        dbname,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'artifact_admissions_detected_format_check'",
    )
    assert "'pdf'" in definition, definition
    # The vocabulary stays closed: no wildcard, no regex, and every earlier
    # format is still allowed (0015's rows remain valid).
    assert "LIKE" not in definition and "~" not in definition, definition
    for code in ("'text'", "'csv'", "'json'", "'xlsx'", "'zip'", "'binary'", "'unknown'"):
        assert code in definition, (code, definition)


def test_worker_still_has_no_platform_privileges_after_0038(migrated_database) -> None:
    _dsn, dbname = migrated_database
    granted = st.sql(
        dbname,
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform'",
    )
    assert granted == "0"
