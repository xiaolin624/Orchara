package modelregistry

// F06/T039: the deployer-registered model catalog.
//
// The catalog is the *only* source of endpoint and credential references a
// model registration may name. It is deployment configuration (environment
// JSON), parsed strictly and failed closed: a malformed catalog is a startup
// error, never an empty catalog that silently accepts nothing (or everything).
//
// The catalog carries no secret value by construction: a credential entry is an
// id plus a human description, and the platform never resolves it to bytes.
// Resolving a reference to an actual secret happens in the deployment adapter,
// not here, so nothing in this process can leak one into an API body.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"sort"
	"strings"
)

// Catalog environment variable names and their dev-stub defaults.
const (
	EnvModelEndpoints  = "ORCHARA_MODEL_ENDPOINTS"
	EnvCredentialRefs  = "ORCHARA_CREDENTIAL_REFS"
	DefaultEndpointID  = "stub-endpoint"
	DefaultCredential  = "stub-credential"
	DefaultDestination = "local-stub"
)

// referenceID is the shape a catalog id and a model profile's references must
// have. Restricting it to lowercase slug characters keeps a reference from
// ever looking like a URL, a path or a secret.
var referenceID = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,63}$`)

// envName is the shape of a deployment environment variable name. A credential
// entry may name one; the admin projection never does.
var envName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)

// Endpoint is one deployer-registered model destination. It names a data
// destination (what leaves the platform and where it lands) and the credential
// reference used to reach it, never the credential itself.
type Endpoint struct {
	ID              string `json:"id"`
	DataDestination string `json:"data_destination"`
	CredentialRef   string `json:"credential_ref"`
	Description     string `json:"description,omitempty"`
}

// CredentialRef is a pre-registered credential reference. It deliberately has
// no value field: the catalog can only say a reference exists, what it is for,
// and (optionally) which deployment environment variable holds the value. The
// value itself is read inside Go by the provider adapter and is never part of
// any API projection.
type CredentialRef struct {
	ID          string `json:"id"`
	Description string `json:"description"`
	// Env is an optional deployment environment variable name. It is a
	// reference name, never a secret value, and the admin projection does not
	// expose it.
	Env string `json:"env,omitempty"`
}

// Catalog is the immutable set of endpoints and credential references.
type Catalog struct {
	endpoints   map[string]Endpoint
	credentials map[string]CredentialRef
}

// FromEnv builds the catalog from lookup (normally os.Getenv).
//
// Both variables unset is the documented dev stub. One set and the other unset
// is refused: a deployment that has endpoints but no credentials (or the
// reverse) cannot register anything coherently, and silently inventing the
// missing half is exactly the kind of default that hides a misconfiguration.
func FromEnv(lookup func(string) string) (*Catalog, error) {
	if lookup == nil {
		lookup = func(string) string { return "" }
	}
	rawEndpoints := strings.TrimSpace(lookup(EnvModelEndpoints))
	rawCredentials := strings.TrimSpace(lookup(EnvCredentialRefs))
	switch {
	case rawEndpoints == "" && rawCredentials == "":
		return newCatalog(
			[]Endpoint{{
				ID:              DefaultEndpointID,
				DataDestination: DefaultDestination,
				CredentialRef:   DefaultCredential,
				Description:     "dev stub (no real endpoint)",
			}},
			[]CredentialRef{{
				ID:          DefaultCredential,
				Description: "dev stub (no real secret)",
			}},
		)
	case rawEndpoints == "" || rawCredentials == "":
		return nil, fmt.Errorf(
			"model catalog is incomplete: %s and %s must be set together",
			EnvModelEndpoints, EnvCredentialRefs,
		)
	}
	var endpoints []Endpoint
	if err := strictDecode(rawEndpoints, &endpoints, EnvModelEndpoints); err != nil {
		return nil, err
	}
	var credentials []CredentialRef
	if err := strictDecode(rawCredentials, &credentials, EnvCredentialRefs); err != nil {
		return nil, err
	}
	return newCatalog(endpoints, credentials)
}

// strictDecode rejects unknown fields and trailing data: a catalog with a typo
// must not be read as "that field was not important".
func strictDecode(raw string, target any, source string) error {
	decoder := json.NewDecoder(bytes.NewReader([]byte(raw)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("%s is not valid JSON for the model catalog: %w", source, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return fmt.Errorf("%s has trailing data after the catalog array", source)
	}
	return nil
}

func newCatalog(endpoints []Endpoint, credentials []CredentialRef) (*Catalog, error) {
	catalog := &Catalog{
		endpoints:   make(map[string]Endpoint, len(endpoints)),
		credentials: make(map[string]CredentialRef, len(credentials)),
	}
	for _, credential := range credentials {
		if err := validateReferenceID(credential.ID, "credential id"); err != nil {
			return nil, err
		}
		if strings.TrimSpace(credential.Description) == "" || len(credential.Description) > 200 {
			return nil, fmt.Errorf("credential %q must have a description of 1-200 characters", credential.ID)
		}
		if credential.Env != "" && !envName.MatchString(credential.Env) {
			return nil, fmt.Errorf("credential %q names an invalid deployment environment variable", credential.ID)
		}
		if _, exists := catalog.credentials[credential.ID]; exists {
			return nil, fmt.Errorf("credential %q is registered twice", credential.ID)
		}
		catalog.credentials[credential.ID] = credential
	}
	for _, endpoint := range endpoints {
		if err := validateReferenceID(endpoint.ID, "endpoint id"); err != nil {
			return nil, err
		}
		if _, exists := catalog.endpoints[endpoint.ID]; exists {
			return nil, fmt.Errorf("endpoint %q is registered twice", endpoint.ID)
		}
		if strings.TrimSpace(endpoint.DataDestination) == "" || len(endpoint.DataDestination) > 100 {
			return nil, fmt.Errorf("endpoint %q must name a data destination", endpoint.ID)
		}
		if _, ok := catalog.credentials[endpoint.CredentialRef]; !ok {
			return nil, fmt.Errorf(
				"endpoint %q names credential reference %q, which is not registered",
				endpoint.ID, endpoint.CredentialRef,
			)
		}
		catalog.endpoints[endpoint.ID] = endpoint
	}
	if len(catalog.credentials) == 0 {
		return nil, fmt.Errorf("the model catalog registers no credential references")
	}
	return catalog, nil
}

func validateReferenceID(value, what string) error {
	if !referenceID.MatchString(value) {
		return fmt.Errorf(
			"%s %q must match %s (it is a reference, never a URL, path or secret)",
			what, value, referenceID.String(),
		)
	}
	return nil
}

// Endpoint returns the registered endpoint with that id.
func (c *Catalog) Endpoint(id string) (Endpoint, bool) {
	if c == nil {
		return Endpoint{}, false
	}
	endpoint, ok := c.endpoints[id]
	return endpoint, ok
}

// Credential returns the registered credential reference with that id.
func (c *Catalog) Credential(id string) (CredentialRef, bool) {
	if c == nil {
		return CredentialRef{}, false
	}
	credential, ok := c.credentials[id]
	return credential, ok
}

// HasEndpoint reports whether id is a registered endpoint reference.
func (c *Catalog) HasEndpoint(id string) bool {
	_, ok := c.Endpoint(id)
	return ok
}

// HasCredential reports whether id is a registered credential reference.
func (c *Catalog) HasCredential(id string) bool {
	_, ok := c.Credential(id)
	return ok
}

// CredentialEnv returns the deployment environment variable a credential
// reference names, if it names one.
func (c *Catalog) CredentialEnv(id string) (string, bool) {
	credential, ok := c.Credential(id)
	if !ok || credential.Env == "" {
		return "", false
	}
	return credential.Env, true
}

// Endpoints lists every endpoint, ordered by id.
func (c *Catalog) Endpoints() []Endpoint {
	if c == nil {
		return nil
	}
	ids := make([]string, 0, len(c.endpoints))
	for id := range c.endpoints {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]Endpoint, 0, len(ids))
	for _, id := range ids {
		out = append(out, c.endpoints[id])
	}
	return out
}

// Credentials lists every credential reference, ordered by id.
func (c *Catalog) Credentials() []CredentialRef {
	if c == nil {
		return nil
	}
	ids := make([]string, 0, len(c.credentials))
	for id := range c.credentials {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]CredentialRef, 0, len(ids))
	for _, id := range ids {
		out = append(out, c.credentials[id])
	}
	return out
}
