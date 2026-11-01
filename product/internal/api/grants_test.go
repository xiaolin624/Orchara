package api

import (
	"encoding/json"
	"errors"
	"testing"
	"time"

	"orchara/platform/internal/policy"
)

const (
	validPrincipalID = "11111111-1111-4111-8111-111111111111"
	validResourceID  = "22222222-2222-4222-8222-222222222222"
)

func validGrantRequest() grantRequest {
	return grantRequest{
		PrincipalID:  validPrincipalID,
		ResourceType: "application",
		ResourceID:   validResourceID,
		Action:       "read",
	}
}

func TestGrantParamsAcceptsTheContractBody(t *testing.T) {
	now := time.Date(2026, 8, 9, 10, 11, 12, 0, time.UTC)
	expires := now.Add(24 * time.Hour).Format(time.RFC3339)
	request := validGrantRequest()
	request.ExpiresAt = &expires

	params, err := grantParams(request, now)
	if err != nil {
		t.Fatalf("the contract body was rejected: %v", err)
	}
	if params.ExpiresAt == nil || !params.ExpiresAt.Equal(now.Add(24*time.Hour)) {
		t.Fatalf("expires_at was not parsed: %+v", params.ExpiresAt)
	}

	plain := validGrantRequest()
	if _, err := grantParams(plain, now); err != nil {
		t.Fatalf("a grant without an expiry was rejected: %v", err)
	}
}

func TestGrantParamsReportsTheOffendingField(t *testing.T) {
	now := time.Date(2026, 8, 9, 10, 11, 12, 0, time.UTC)
	badExpiry := "the day after tomorrow"
	pastExpiry := now.Add(-time.Minute).Format(time.RFC3339)
	cases := []struct {
		name   string
		mutate func(*grantRequest)
		field  string
	}{
		{"malformed principal", func(request *grantRequest) { request.PrincipalID = "nope" }, "principal_id"},
		{"unknown resource type", func(request *grantRequest) { request.ResourceType = "memory" }, "resource_type"},
		{"malformed resource", func(request *grantRequest) { request.ResourceID = "nope" }, "resource_id"},
		{"unknown action", func(request *grantRequest) { request.Action = "write" }, "action"},
		{"unparsable expiry", func(request *grantRequest) { request.ExpiresAt = &badExpiry }, "expires_at"},
		{"past expiry", func(request *grantRequest) { request.ExpiresAt = &pastExpiry }, "expires_at"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			request := validGrantRequest()
			testCase.mutate(&request)
			_, err := grantParams(request, now)
			details := validationDetails(err)
			if details == nil || details["field"] != testCase.field {
				t.Fatalf("%s reported %v, want field %q", testCase.name, details, testCase.field)
			}
			var validation *policy.ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("%s is not a typed validation error: %v", testCase.name, err)
			}
		})
	}
}

func TestApplicationViewIsExactlyTheContractSchema(t *testing.T) {
	view := applicationView{ID: validResourceID, Name: "demo"}
	raw, err := json.Marshal(view)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	want := `{"id":"22222222-2222-4222-8222-222222222222","name":"demo","active_version_id":null}`
	if string(raw) != want {
		t.Fatalf("Application JSON shape changed:\n got %s\nwant %s", raw, want)
	}
}

func TestApplicationListUsesAnArrayAndANullableCursor(t *testing.T) {
	raw, err := json.Marshal(applicationListResponse{Items: []applicationView{}})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	want := `{"items":[],"next_cursor":null}`
	if string(raw) != want {
		t.Fatalf("list envelope shape changed:\n got %s\nwant %s", raw, want)
	}
}
