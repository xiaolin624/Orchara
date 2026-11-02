// Package migrations embeds the numbered SQL migration chain.
//
// The SQL files are the only source of schema history. `internal/db` applies
// them in version order and records a SHA-256 checksum per file, so editing an
// already-applied migration fails loudly instead of drifting from a database
// that was built earlier.
package migrations

import "embed"

// Files is the embedded migration chain (`migrations/0001_...sql` and up).
//
//go:embed migrations/*.sql
var Files embed.FS
