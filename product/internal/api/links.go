package api

import (
	"errors"
	"io"
	"net/http"
	"strconv"

	"orchara/platform/internal/objectstore"
)

// Presigned object links.
//
// The token is the entire authority: there is no session, no CSRF token and no
// list endpoint. That is deliberate (D2): a worker, a maintenance process or a
// future browser uploader receives a URL for one operation on one object, and
// nothing about the platform's object namespace is discoverable.
//
// Every refusal is a 404 with the same body: expired, tampered, signed with
// another key, the wrong operation for this route, or a PUT aimed at a sealed
// key. A caller cannot learn from the answer whether the object exists.

func (s *Server) handleObjectLinkPut(w http.ResponseWriter, r *http.Request) {
	claims, err := s.signer.Verify(r.PathValue("token"), objectstore.OpPut)
	if err != nil {
		writeObjectLinkNotFound(w, r)
		return
	}
	if s.objects == nil {
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
		return
	}
	// The store streams into a temp file and enforces claims.MaxBytes exactly
	// (0 accepts only an empty body); a partial or oversized write leaves no
	// object behind. No client-chosen path is involved.
	object, err := s.objects.PutStaging(claims.Key, r.Body, claims.MaxBytes)
	switch {
	case errors.Is(err, objectstore.ErrTooLarge):
		writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
			"the object exceeds the size reserved for this link")
		return
	case errors.Is(err, objectstore.ErrInvalidKey), errors.Is(err, objectstore.ErrNotConfigured):
		writeObjectLinkNotFound(w, r)
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	// The hash is not returned to the uploader: the artifact row records the
	// declared hash at complete time, and the finalize guard recomputes it from
	// the stored bytes.
	_ = object
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleObjectLinkGet(w http.ResponseWriter, r *http.Request) {
	claims, err := s.signer.Verify(r.PathValue("token"), objectstore.OpGet)
	if err != nil {
		writeObjectLinkNotFound(w, r)
		return
	}
	if s.objects == nil {
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
		return
	}
	reader, size, err := s.objects.Open(claims.Key)
	if err != nil || size < 0 || size > objectstore.MaxObjectBytes {
		if err != nil && !errors.Is(err, objectstore.ErrNotExist) &&
			!errors.Is(err, objectstore.ErrInvalidKey) && !errors.Is(err, objectstore.ErrNotConfigured) {
			s.internalError(w, r, err)
			return
		}
		writeObjectLinkNotFound(w, r)
		return
	}
	defer func() { _ = reader.Close() }()
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(size, 10))
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Disposition", "attachment")
	w.WriteHeader(http.StatusOK)
	if _, err := io.Copy(w, io.LimitReader(reader, objectstore.MaxObjectBytes+1)); err != nil {
		// The status and the first bytes are already on the wire; the request log
		// records the failure and the caller sees a truncated body.
		s.logger.Printf("request_id=%s object link read failed: %v", RequestID(r.Context()), err)
	}
}

// writeObjectLinkNotFound is the single refusal for every invalid link.
func writeObjectLinkNotFound(w http.ResponseWriter, r *http.Request) {
	writeError(w, r, http.StatusNotFound, "not_found", "no such object link")
}
