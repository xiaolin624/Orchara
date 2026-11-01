// Command jcs prints the RFC 8785 canonical form and SHA-256 of a JSON file.
//
// Output contract (shared with the Python CLI so tests can compare them):
//
//	{"canonical":"...","sha256":"...","length":N}   exit 0
//	{"error":"code","message":"..."}                exit 1
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

type successPayload struct {
	Canonical string `json:"canonical"`
	SHA256    string `json:"sha256"`
	Length    int    `json:"length"`
}

func readInput(args []string) ([]byte, error) {
	if len(args) > 1 {
		return nil, errors.New("usage: jcs [file]")
	}
	if len(args) == 1 {
		return os.ReadFile(args[0])
	}
	return readAllStdin()
}

func readAllStdin() ([]byte, error) {
	var buffer []byte
	chunk := make([]byte, 4096)
	for {
		n, err := os.Stdin.Read(chunk)
		buffer = append(buffer, chunk[:n]...)
		if err != nil {
			break
		}
	}
	return buffer, nil
}

func main() {
	raw, err := readInput(os.Args[1:])
	if err != nil {
		emitError(&JCSError{Code: "io_error", Message: err.Error()})
		os.Exit(1)
	}

	canonical, err := CanonicalizeBytes(raw)
	if err != nil {
		var jcsErr *JCSError
		if errors.As(err, &jcsErr) {
			emitError(jcsErr)
		} else {
			emitError(&JCSError{Code: "invalid_json", Message: err.Error()})
		}
		os.Exit(1)
	}

	emit(successPayload{
		Canonical: string(canonical),
		SHA256:    SHA256Hex(canonical),
		Length:    len(canonical),
	})
}

func emit(payload any) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(payload); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
}

func emitError(err *JCSError) { emit(err) }
