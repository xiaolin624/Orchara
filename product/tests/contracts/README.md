# Contract-level fixtures

`snapshots/` holds the F02A/T025 golden execution manifests and their canonical
bytes. See `snapshots/README.md` for the format, the regeneration command and the
verify-bytes mechanism.

The negative cases (dependency cycle, unknown/unready dependency, foreign
sub-app without an ACL, definition error located to a node) are real-stack tests
in `tests/f02a/` because they need seeded PostgreSQL rows; they are not static
files here.
