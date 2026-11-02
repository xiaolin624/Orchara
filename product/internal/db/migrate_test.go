package db

import (
	"crypto/sha256"
	"encoding/hex"
	"testing"
	"testing/fstest"
)

func TestLoadFSSortsByVersionAndHashesTheBytes(t *testing.T) {
	files := fstest.MapFS{
		"migrations/0002_two.sql": {Data: []byte("SELECT 2;\n")},
		"migrations/0001_one.sql": {Data: []byte("SELECT 1;\n")},
		"migrations/notes.txt":    {Data: []byte("ignored")},
	}
	chain, err := LoadFS(files, "migrations")
	if err != nil {
		t.Fatalf("LoadFS: %v", err)
	}
	if len(chain) != 2 {
		t.Fatalf("loaded %d migrations, want 2", len(chain))
	}
	if chain[0].Version != "0001" || chain[0].Name != "0001_one.sql" {
		t.Fatalf("wrong order or name: %+v", chain[0])
	}
	if chain[1].Version != "0002" {
		t.Fatalf("wrong order: %+v", chain[1])
	}
	sum := sha256.Sum256([]byte("SELECT 1;\n"))
	if want := hex.EncodeToString(sum[:]); chain[0].SHA256 != want {
		t.Fatalf("sha256 = %s, want %s", chain[0].SHA256, want)
	}
}

func TestLoadFSRejectsAMalformedName(t *testing.T) {
	files := fstest.MapFS{"migrations/identity.sql": {Data: []byte("SELECT 1;")}}
	if _, err := LoadFS(files, "migrations"); err == nil {
		t.Fatal("a file without a version prefix was accepted")
	}
}

func TestLoadFSRejectsDuplicateVersions(t *testing.T) {
	files := fstest.MapFS{
		"migrations/0001_a.sql": {Data: []byte("SELECT 1;")},
		"migrations/0001_b.sql": {Data: []byte("SELECT 2;")},
	}
	if _, err := LoadFS(files, "migrations"); err == nil {
		t.Fatal("two files with the same version were accepted")
	}
}

func TestLoadFSRejectsAnEmptyDirectory(t *testing.T) {
	if _, err := LoadFS(fstest.MapFS{}, "."); err == nil {
		t.Fatal("an empty migration directory was accepted")
	}
}

func TestVersionOf(t *testing.T) {
	cases := []struct {
		name    string
		version string
		ok      bool
	}{
		{"0006_identity_core.sql", "0006", true},
		{"12_multi_digit.sql", "12", true},
		{"0006-.sql", "", false},
		{"0006_identity_core.SQL", "", false},
		{"_x.sql", "", false},
		{"0006_identity_core.sql.bak", "", false},
	}
	for _, tc := range cases {
		version, err := versionOf(tc.name)
		if tc.ok && err != nil {
			t.Fatalf("%s: unexpected error: %v", tc.name, err)
		}
		if !tc.ok && err == nil {
			t.Fatalf("%s: expected an error", tc.name)
		}
		if version != tc.version {
			t.Fatalf("%s: version = %q, want %q", tc.name, version, tc.version)
		}
	}
}
