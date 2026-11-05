package api

import (
	"errors"
	"log"
	"net/http"
	"path/filepath"
	"strconv"
	"time"

	"orchara/platform/internal/approvals"
	"orchara/platform/internal/artifacts"
	"orchara/platform/internal/background"
	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/config"
	"orchara/platform/internal/connectors"
	"orchara/platform/internal/embedding"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/knowledge"
	"orchara/platform/internal/memory"
	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/modelregistry"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/policy"
	"orchara/platform/internal/toolruns"
	"orchara/platform/internal/toolversions"
)

// Server serves the F01 identity endpoints, the F02A draft/version paths, the
// F00B background-job surface and the static pages that drive them.
type Server struct {
	service   *identity.Service
	policy    *policy.Authorizer
	catalog   *catalog.Reader
	jobs      *background.Controller
	artifacts *background.ArtifactStore
	// execution owns the F03 claim/heartbeat/attempt-exit primitive.
	execution *execution.Service
	// evaluationRuns is F13A/T052's job-scoped evaluation run bridge: it creates
	// a case's run as the evaluation owner and reads the independent truth the
	// checker decides on.
	evaluationRuns *execution.EvaluationRuns
	// runArtifacts owns F05/T033's run-bound upload/download/status/register:
	// the supervisor may claim an attempt, but only the live attempt that
	// reserved an artifact can complete and register it.
	runArtifacts *execution.RunArtifactStore
	// userArtifacts is the T028 user-facing artifact service (distinct from the
	// job-scoped ArtifactStore above: different purposes, different ACL).
	userArtifacts *artifacts.Service
	// knowledge is the F10/T055 owner-scoped knowledge base/document/retrieval
	// service. It never activates a document; the background completion guard does.
	knowledge *knowledge.Service
	// toolRuns is the T030 preset-tool run service (non-contract bridge).
	toolRuns *toolruns.Service
	// toolVersions is the F05/T037 submission/build/review service.
	toolVersions *toolversions.Service
	models       *modelproxy.Service
	// modelRegistry is the F06/T039 registration and validation service.
	modelRegistry *modelregistry.Service
	// capabilities is the F06/T041 run-scoped controlled-capability boundary.
	capabilities *capabilities.Service
	// connectors is the F06/T048 versioned connector registry (admin-only).
	connectors *connectors.Service
	// approvals is the F06/T050 exact-parameter approval lifecycle.
	approvals *approvals.Service
	// preview is the F06/T044 temporary in-process model stream preview hub. It
	// is not persisted and not shared across processes.
	preview *previewHub
	// memory is the F09/T054 personal memory store (candidates + confirmed
	// memories). Its nil-ness is not a supported state: the store is always
	// built, and a process without an object store fails closed per request.
	memory  *memory.Service
	objects *objectstore.Store
	signer  *objectstore.Signer
	// manifest carries the deployment inputs (worker image digest, runtime lock
	// path) the validate endpoint needs. Absent inputs are not a startup error;
	// the endpoint answers 503 compiler_unconfigured instead.
	manifest     compiler.Inputs
	webDir       string
	secureCookie bool
	logger       *log.Logger
	limiter      *loginLimiter
	// F03/T032: the SSE replay window and its polling/heartbeat cadence. They are
	// deployment knobs (ORCHARA_RUN_EVENT_*), normalised in New.
	runEventRetention time.Duration
	runEventPoll      time.Duration
	runEventHeartbeat time.Duration
}

// Deps is everything the HTTP surface needs from the process that owns it.
//
// It is a struct rather than a positional argument list because the surface has
// grown with each ticket (identity, catalog, background jobs, object links,
// model proxy, manifest inputs) and a ten-argument constructor makes a swapped
// pair of same-typed arguments compile silently. Named fields keep the wiring
// legible and let a caller opt out of optional pieces explicitly.
type Deps struct {
	// Service is the identity service; required.
	Service *identity.Service
	// WebDir is where the static pages and assets are served from (ORCHARA_WEB_DIR).
	WebDir string
	// Profile selects cookie security and is recorded for the process profile.
	Profile config.Profile
	// Logger receives request-independent diagnostics; nil means discard.
	Logger *log.Logger
	// Objects/Signer may be nil: every operation that needs bytes or a signed
	// link then fails closed instead of skipping verification.
	Objects *objectstore.Store
	Signer  *objectstore.Signer
	// Providers is the model-proxy provider set built from deployment configuration.
	Providers *modelproxy.Providers
	// Connectors is the F06/T048 registry and guarded egress client. Nil fails
	// closed: no connector capability can be invoked.
	Connectors *connectors.Service
	// Catalog is the deployer-registered model endpoint/credential catalog. Nil
	// means the documented dev-stub default, never an open catalog.
	Catalog *modelregistry.Catalog
	// Embedder is the F10 embedding seam. Nil means the process reads
	// ORCHARA_EMBEDDING_PROVIDER/ORCHARA_EMBEDDING_MODEL (empty = the
	// deterministic local embedder recorded as local-hash-v1). A configured real
	// provider without an adapter fails closed, never as a silent stub.
	Embedder *embedding.Service
	// Answerer is the F10/T064 labelled answer generator. Nil means the
	// extractive stub wired by knowledge.New (no model provider exists here).
	Answerer knowledge.AnswerGenerator
	// RetrievalCache is the F10/T067 retrieval cache configuration. Nil means the
	// knowledge.New default (enabled, 15 minutes); cmd/api always passes the
	// value derived from ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE (an unknown value is a
	// startup error there, never a silent default).
	RetrievalCache *knowledge.CacheOptions
	// PublicURL is the absolute base URL signed links are minted against.
	PublicURL string
	// RetryBackoff is the background-job retry base delay.
	RetryBackoff time.Duration
	// ExecutionLeaseTTL is the F03 claim lease length. Zero falls back to
	// execution.DefaultLeaseTTL (30s); a value outside the documented bounds is
	// normalised by the execution service.
	ExecutionLeaseTTL time.Duration
	// Manifest carries the deployment inputs the validate endpoint needs; absent
	// inputs are a 503, not a startup error.
	Manifest compiler.Inputs
	// Notifier is the best-effort background wakeup hook. Nil means NopNotifier:
	// losing every notification is a supported state because claim polls PostgreSQL.
	Notifier background.Notifier
	// RunEventRetention is how far back GET /runs/{id}/events can replay before
	// answering 410 event_cursor_expired. Zero falls back to the documented 24h.
	RunEventRetention time.Duration
	// RunEventPollInterval is the idle SSE PostgreSQL poll cadence. Zero falls
	// back to 500ms.
	RunEventPollInterval time.Duration
	// RunEventHeartbeatInterval is the idle SSE comment cadence. Zero falls back
	// to the documented 15s.
	RunEventHeartbeatInterval time.Duration
	// MemoryCandidateTTL is how long a proposed personal-memory candidate waits
	// for the human decision (ORCHARA_MEMORY_CANDIDATE_TTL). Zero falls back to
	// memory.DefaultCandidateTTL (7 days).
	MemoryCandidateTTL time.Duration
}

// New builds the HTTP surface. See Deps for each dependency's contract.
func New(deps Deps) *Server {
	logger := deps.Logger
	if logger == nil {
		logger = log.New(nullWriter{}, "", 0)
	}
	notifier := deps.Notifier
	if notifier == nil {
		notifier = background.NopNotifier{}
	}
	authorizer := policy.New(deps.Service.DB)
	executionService := execution.New(
		deps.Service.DB, deps.Objects, deps.Signer, deps.PublicURL, deps.ExecutionLeaseTTL,
	)
	// local_sequence diagnostics go to the process log, never the API body.
	executionService.Logger = logger
	catalogRefs := deps.Catalog
	if catalogRefs == nil {
		// The dev-stub default is deterministic and cannot fail; a nil catalog
		// must never mean "accept anything".
		catalogRefs, _ = modelregistry.FromEnv(nil)
	}
	modelRegistry, err := modelregistry.New(deps.Service.DB, deps.Objects, catalogRefs)
	if err != nil {
		// Only reachable for an impossible nil default; keep the registration
		// surface failing closed instead of panicking at startup.
		modelRegistry = nil
	}
	capabilityService := capabilities.New(deps.Service.DB, deps.Objects, deps.Providers)
	capabilityService.Connectors = deps.Connectors
	// F06/T051: the T036 bridge is the one writer of the unknown ledger row, and
	// the reconcile transaction re-drives the same finalizer through it.
	capabilityService.Unknowns = executionService
	capabilityService.Reconciler = executionService
	approvalService := approvals.New(deps.Service.DB, deps.Objects, executionService)
	capabilityService.Approvals = approvalService
	embedder := deps.Embedder
	if embedder == nil {
		embedder = embedding.FromEnv()
	}
	jobsController := background.New(deps.Service.DB, deps.Objects, deps.RetryBackoff, notifier)
	knowledgeService := knowledge.New(deps.Service.DB, deps.Objects, embedder)
	if deps.Answerer != nil {
		knowledgeService.Answerer = deps.Answerer
	}
	if deps.RetrievalCache != nil {
		knowledgeService.Cache = *deps.RetrievalCache
	}
	return &Server{
		service:        deps.Service,
		policy:         authorizer,
		catalog:        catalog.New(deps.Service.DB, authorizer),
		jobs:           jobsController,
		artifacts:      background.NewArtifactStore(deps.Service.DB, deps.Objects, deps.Signer, deps.PublicURL, notifier),
		execution:      executionService,
		evaluationRuns: execution.NewEvaluationRuns(deps.Service.DB, deps.Objects, jobsController),
		runArtifacts:   execution.NewRunArtifactStore(deps.Service.DB, deps.Objects, deps.Signer, deps.PublicURL, notifier),
		userArtifacts:  artifacts.New(deps.Service.DB, deps.Objects, deps.Signer, deps.PublicURL),
		knowledge:      knowledgeService,
		toolRuns:       toolruns.New(deps.Service.DB, notifier),
		toolVersions:   toolversions.New(deps.Service.DB),
		models:         modelproxy.NewService(deps.Service.DB, deps.Objects, deps.Providers, catalogRefs),
		modelRegistry:  modelRegistry,
		capabilities:   capabilityService,
		connectors:     deps.Connectors,
		approvals:      approvalService,
		preview:        newPreviewHub(),
		memory: memory.New(
			deps.Service.DB, deps.Objects,
			catalog.New(deps.Service.DB, authorizer), deps.MemoryCandidateTTL,
		),
		objects:           deps.Objects,
		signer:            deps.Signer,
		manifest:          deps.Manifest,
		webDir:            deps.WebDir,
		secureCookie:      SecureCookie(deps.Profile),
		logger:            logger,
		limiter:           newLoginLimiter(loginFailureLimit, loginFailureWindow, time.Now),
		runEventRetention: durationOrDefault(deps.RunEventRetention, DefaultRunEventRetention),
		runEventPoll:      durationOrDefault(deps.RunEventPollInterval, DefaultRunEventPollInterval),
		runEventHeartbeat: durationOrDefault(deps.RunEventHeartbeatInterval, DefaultRunEventHeartbeatInterval),
	}
}

// durationOrDefault keeps a zero dependency (an older caller) on the documented
// deployment default instead of a zero-duration ticker panic.
func durationOrDefault(value, fallback time.Duration) time.Duration {
	if value <= 0 {
		return fallback
	}
	return value
}

// Register adds the identity routes to mux.
//
// Every authenticated route goes through Server.authorize, so the CSRF and role
// rules live in one place; /auth/login and /auth/activate are unauthenticated by
// contract and therefore have no session (and no CSRF token) to check.
func (s *Server) Register(mux *http.ServeMux) {
	mux.HandleFunc("POST /api/v1/auth/login", s.handleLogin)
	mux.HandleFunc("POST /api/v1/auth/logout", s.handleLogout)
	mux.HandleFunc("GET /api/v1/auth/me", s.handleMe)
	mux.HandleFunc("POST /api/v1/auth/activate", s.handleActivate)
	mux.HandleFunc("POST /api/v1/admin/users", s.handleInviteUser)

	// T020: named service accounts, scoped bearer keys and the personal-data
	// boundary shared by every memory endpoint (F09/T054).
	mux.HandleFunc("POST /api/v1/admin/service-accounts", s.handleCreateServiceAccount)
	mux.HandleFunc("POST /api/v1/admin/api-keys", s.handleCreateAPIKey)
	mux.HandleFunc("DELETE /api/v1/admin/api-keys/{key_id}", s.handleRevokeAPIKey)
	// F09/T054: the real personal-memory read/write model. A service credential
	// is refused with 403 personal_scope_forbidden on every one of them.
	// F09/T058 adds the revisioned detail/update/delete surface; the detail read
	// is the only strong-ETag read on a memory.
	mux.HandleFunc("POST /api/v1/memories", s.handleCreateMemory)
	mux.HandleFunc("GET /api/v1/memories", s.handleListMemories)
	mux.HandleFunc("GET /api/v1/memories/{memory_id}", s.handleGetMemory)
	mux.HandleFunc("PATCH /api/v1/memories/{memory_id}", s.handleUpdateMemory)
	mux.HandleFunc("DELETE /api/v1/memories/{memory_id}", s.handleDeleteMemory)
	mux.HandleFunc("GET /api/v1/memory-candidates", s.handleListMemoryCandidates)
	mux.HandleFunc("POST /api/v1/memory-candidates/{candidate_id}/decision",
		s.handleDecideMemoryCandidate)

	// T021: disable/enable a principal. Disabling revokes the principal's
	// sessions and API keys (including the keys of service accounts it is
	// responsible for), bumps the policy epoch and audits, in one transaction.
	mux.HandleFunc("PATCH /api/v1/admin/principals/{principal_id}", s.handleUpdatePrincipal)

	// T019: resource grants, role updates and the ACL-filtered applications read.
	mux.HandleFunc("POST /api/v1/admin/grants", s.handleGrant)
	mux.HandleFunc("DELETE /api/v1/admin/grants/{grant_id}", s.handleRevokeGrant)
	mux.HandleFunc("PUT /api/v1/admin/users/{principal_id}/roles", s.handleSetRoles)
	mux.HandleFunc("GET /api/v1/applications", s.handleListApplications)
	mux.HandleFunc("GET /api/v1/applications/{application_id}", s.handleGetApplication)

	// F02A/T023: the creator's draft path and the immutable version read model.
	// The version list/detail join applications and apply the same ACL as the
	// application read; the draft write is owner-only with ETag/If-Match.
	mux.HandleFunc("POST /api/v1/applications", s.handleCreateApplication)
	mux.HandleFunc("GET /api/v1/applications/{application_id}/draft", s.handleGetDraft)
	mux.HandleFunc("PUT /api/v1/applications/{application_id}/draft", s.handleSaveDraft)
	mux.HandleFunc("GET /api/v1/applications/{application_id}/versions", s.handleListVersions)
	mux.HandleFunc("GET /api/v1/versions/{version_id}", s.handleGetVersion)

	// F04A/T049: copy the server-controlled sales template into the caller's own
	// private application. Non-contract and not a publication: the caller then
	// uses the normal draft/debug path, and F02B still owns shared versions.
	mux.HandleFunc("POST /api/v1/templates/{template_id}/applications", s.handleInstantiateTemplate)

	// F02A/T025: compile the current draft, resolve its fixed dependency closure
	// and return located errors. A valid draft also returns the server-side
	// manifest hash in X-Manifest-SHA256 (the 200 schema has no field for it; the
	// extension is documented in ADR 0004 §26) and seals the canonical bytes.
	mux.HandleFunc("POST /api/v1/applications/{application_id}/validate", s.handleValidateApplication)

	// F02A/T027: private debug submission (owner-only, Idempotency-Key required,
	// one frozen snapshot + queued run + ready start job) and the owner-only run
	// read. The fixed manifest hash travels in X-Manifest-SHA256 on both, because
	// the contract Run schema has no field for it (ADR 0004 §38).
	mux.HandleFunc("POST /api/v1/applications/{application_id}/debug-runs", s.handleDebugDraft)
	mux.HandleFunc("GET /api/v1/runs", s.handleListRuns)
	mux.HandleFunc("GET /api/v1/runs/{run_id}", s.handleGetRun)
	// F03/T036: owner-only, idempotent cancel intent. 202 with the contract Run;
	// "accepted" never means the external effect was undone.
	mux.HandleFunc("POST /api/v1/runs/{run_id}/cancel", s.handleCancelRun)
	// F03/T032: the owner-only durable event stream. Last-Event-ID is this Run's
	// local seq; the handler is SSE, not JSON.
	mux.HandleFunc("GET /api/v1/runs/{run_id}/events", s.handleRunEvents)
	// F05/T033: the owner-only run artifact list.
	mux.HandleFunc("GET /api/v1/runs/{run_id}/artifacts", s.handleListRunArtifacts)
	// F04A/T042: the owner-only per-node data/file/log provenance read
	// (non-contract). Content stays on the artifact content/download routes.
	mux.HandleFunc("GET /api/v1/runs/{run_id}/node-results", s.handleListRunNodeResults)
	// F06/T041: the owner-only controlled-capability ledger read. Non-contract
	// (no public capability invocation exists); the service projection carries no
	// params or receipt bodies.
	mux.HandleFunc("GET /api/v1/runs/{run_id}/capabilities", s.handleRunCapabilities)
	// F06/T044: the temporary owner-only model stream preview (non-contract). It
	// is separate from the durable Run SSE above: it replays the current
	// generation from in-process memory only and closes on final/error.
	mux.HandleFunc("GET /api/v1/runs/{run_id}/model-preview", s.handleRunModelPreview)

	// F00B/T022: the redacted diagnostic read for a background job's owner or an
	// administrator. The internal claim/heartbeat/result routes live on the
	// separate mTLS listener (RegisterInternal).
	mux.HandleFunc("GET /api/v1/background-jobs/{job_id}", s.handleGetBackgroundJob)

	// F00B/T026: the evaluation business state and its immutable report. Both
	// are owner-or-admin (everyone else 404) and project only contract fields;
	// the report read never returns the report body or an artifact path.
	mux.HandleFunc("GET /api/v1/evaluations/{evaluation_id}", s.handleGetEvaluation)
	mux.HandleFunc("GET /api/v1/evaluation-reports/{report_id}", s.handleGetEvaluationReport)
	// F13A/T056: the per-case view of one sealed report (the documented
	// non-contract extension: artifact ids only). It is owner-only: an
	// administrator is a non-owner of the private result bodies and gets the
	// same 404 as a missing report (DATA-POLICY.md visibility rule; ADR 0017).
	mux.HandleFunc("GET /api/v1/evaluation-reports/{report_id}/cases", s.handleGetEvaluationCases)

	// F13A/T065: the non-contract async progress read (ADR 0019). It returns the
	// honest per-case counters and the business-vs-infrastructure failure class,
	// never a percentage guessed from the evaluation state. Same owner-or-admin
	// authorization as the contract read above.
	mux.HandleFunc("GET /api/v1/evaluations/{evaluation_id}/progress",
		s.handleGetEvaluationProgress)

	// F13A/T062: the administrator-only, non-contract attribution read. It
	// exposes the account, cancellation outcome and the snapshot/manifest/
	// dataset/compiler/worker identity behind a report, and never a sealed object
	// key, a report body or another user's content (ADR 0014). Non-admins get 403
	// before any lookup.
	mux.HandleFunc("GET /api/v1/admin/evaluations/{evaluation_id}/attribution",
		s.handleGetEvaluationAttribution)

	// F13A/T052: the contract's evaluateDraft. The creator names a frozen draft
	// revision and an uploaded, sealed evaluation dataset; the platform freezes
	// the execution snapshot, queues the trusted runner and returns the queued
	// evaluation. A caller can never attest its own verdict: only the completion
	// guard writes a report row.
	mux.HandleFunc("POST /api/v1/applications/{application_id}/evaluations", s.handleSubmitEvaluation)

	// F00B/T024: presigned object transfers. There is deliberately no session
	// requirement here (the HMAC token is the whole authority) and no list
	// endpoint: PUT can only fill a reserved staging key, GET can only read a
	// sealed object, and every bad link is a 404.
	mux.HandleFunc("PUT /object-links/{token}", s.handleObjectLinkPut)
	mux.HandleFunc("GET /object-links/{token}", s.handleObjectLinkGet)

	// F05/T028: user uploads and the owner-only artifact reads. The sealed key
	// is preallocated on the row and never a PUT target; completion only moves
	// uploading -> scanning and enqueues the shared finalize handler.
	mux.HandleFunc("POST /api/v1/artifacts/uploads", s.handleCreateUpload)
	mux.HandleFunc("POST /api/v1/artifacts/{artifact_id}/complete", s.handleCompleteUpload)
	mux.HandleFunc("GET /api/v1/artifacts/{artifact_id}", s.handleGetArtifact)
	mux.HandleFunc("GET /api/v1/artifacts/{artifact_id}/download", s.handleDownloadArtifact)
	mux.HandleFunc("GET /api/v1/artifacts/{artifact_id}/content", s.handleArtifactContent)

	// F05/T030: the trusted preset tool. A documented non-contract bridge over
	// the durable background-job pipeline (T033/F04A bind tools to runs later).
	mux.HandleFunc("POST /api/v1/tool-runs", s.handleCreateToolRun)
	mux.HandleFunc("GET /api/v1/tool-runs/{job_id}", s.handleGetToolRun)

	// F05/T037: tool submission, the contract read model, admin review and the
	// documented non-contract revoke/build-facts endpoints. No route here names a
	// user-supplied command or path.
	mux.HandleFunc("POST /api/v1/tools", s.handleSubmitTool)
	mux.HandleFunc("GET /api/v1/tools", s.handleListTools)
	mux.HandleFunc("GET /api/v1/tools/{tool_version_id}", s.handleGetTool)
	mux.HandleFunc("POST /api/v1/tools/{tool_version_id}/review", s.handleReviewTool)
	mux.HandleFunc("POST /api/v1/tools/{tool_version_id}/revoke", s.handleRevokeTool)
	mux.HandleFunc("GET /api/v1/tools/{tool_version_id}/build", s.handleGetToolBuild)

	// F06/T039: model registration, reads and compatibility validation.
	mux.HandleFunc("POST /api/v1/models", s.handleRegisterModel)
	mux.HandleFunc("GET /api/v1/models", s.handleListModels)
	mux.HandleFunc("GET /api/v1/models/{model_version_id}", s.handleGetModel)
	mux.HandleFunc("POST /api/v1/models/{model_version_id}/validate", s.handleValidateModel)
	// Non-contract admin catalog reads: which deployment references exist.
	mux.HandleFunc("GET /api/v1/admin/model-endpoints", s.handleListModelEndpoints)
	mux.HandleFunc("GET /api/v1/admin/credential-refs", s.handleListCredentialRefs)
	// F06/T048: the versioned connector registry (admin-only; non-contract).
	mux.HandleFunc("POST /api/v1/admin/connectors", s.handleRegisterConnector)
	mux.HandleFunc("GET /api/v1/admin/connectors", s.handleListConnectors)
	mux.HandleFunc("POST /api/v1/admin/connectors/{connector_id}/revoke", s.handleRevokeConnector)

	// F06/T050: exact-parameter approvals. The designated approver (the run
	// owner) is the only human who can read or decide; a stranger sees 404.
	mux.HandleFunc("GET /api/v1/approvals", s.handleListApprovals)
	mux.HandleFunc("GET /api/v1/approvals/{approval_id}", s.handleGetApproval)
	mux.HandleFunc("POST /api/v1/approvals/{approval_id}/decision", s.handleDecideApproval)
	// F06/T051: the owner/approver evidence reconciliation for one unknown
	// external write. The operation id is the binding; no second operation or
	// automatic resend exists on this path.
	mux.HandleFunc("POST /api/v1/operations/{operation_id}/reconcile", s.handleReconcileOperation)

	// F10/T055: knowledge bases, ingestion and the non-contract retrieval and
	// citation reads (ADR 0010). Every route is owner-only; ACL misses are 404.
	mux.HandleFunc("POST /api/v1/knowledge-bases", s.handleCreateKnowledgeBase)
	mux.HandleFunc("GET /api/v1/knowledge-bases", s.handleListKnowledgeBases)
	mux.HandleFunc("GET /api/v1/knowledge-bases/{knowledge_base_id}", s.handleGetKnowledgeBase)
	mux.HandleFunc("POST /api/v1/knowledge-bases/{knowledge_base_id}/documents", s.handleIngestDocument)
	mux.HandleFunc("GET /api/v1/knowledge-bases/{knowledge_base_id}/documents", s.handleListKnowledgeDocuments)
	mux.HandleFunc("GET /api/v1/knowledge-documents/{document_id}", s.handleGetKnowledgeDocument)
	mux.HandleFunc("GET /api/v1/knowledge-documents/{document_id}/diagnostics", s.handleKnowledgeDocumentDiagnostics)
	// F10/T061: the non-contract rebuild entry (ADR 0018). It creates a new
	// index version and one document_ingest job; only the completion guard can
	// move the activation pointer.
	mux.HandleFunc("POST /api/v1/knowledge-documents/{document_id}/reindex", s.handleReindexKnowledgeDocument)
	mux.HandleFunc("POST /api/v1/knowledge-bases/{knowledge_base_id}/retrieval-queries", s.handleKnowledgeRetrievalQuery)
	mux.HandleFunc("POST /api/v1/knowledge-bases/{knowledge_base_id}/answer", s.handleKnowledgeAnswer)
	mux.HandleFunc("GET /api/v1/knowledge-citations/{chunk_id}", s.handleGetKnowledgeCitation)

	mux.HandleFunc("GET /login", s.staticPage("login.html"))
	mux.HandleFunc("GET /admin", s.staticPage("admin.html"))
	mux.HandleFunc("GET /activate", s.staticPage("activate.html"))
	mux.HandleFunc("GET /apps", s.staticPage("apps.html"))
	// F05/T028: the attachment upload page. Like every static route, the path is
	// a compile-time constant and no request input participates in it.
	mux.HandleFunc("GET /attachments", s.staticPage("attachments.html"))
	// F05/T037: the creator tools page and its admin review queue. Like every
	// static route, the path is a compile-time constant.
	mux.HandleFunc("GET /tools", s.staticPage("tools.html"))
	mux.HandleFunc("GET /models", s.staticPage("models.html"))
	// F02A/T023: the editor is served per application so the page never needs a
	// query string (the path segment is the application id the editor edits).
	mux.HandleFunc("GET /apps/{application_id}/edit", s.staticPage("editor.html"))
	// F02A/T027: the debug entry is served per application for the same reason.
	mux.HandleFunc("GET /apps/{application_id}/debug", s.staticPage("debug.html"))
	// F13A/T056: the creator evaluation page. The application id comes from the
	// path; the page never invents one. Its helpers are served as a separate
	// asset so the Node page test executes the same bytes a browser receives.
	mux.HandleFunc("GET /apps/{application_id}/evaluate", s.staticPage("evaluate.html"))
	mux.HandleFunc("GET /assets/evaluate.js", s.staticPage(filepath.Join("assets", "evaluate.js")))
	// F03/T029: the owner-only runs view. Like every static route, the path is a
	// compile-time constant and no request input participates in it.
	mux.HandleFunc("GET /runs", s.staticPage("runs.html"))
	// F13A/T065: the creator's evaluation progress page. The evaluation id is in
	// the path so a refresh is the same shareable view; the page reads the
	// non-contract progress surface (ADR 0019).
	mux.HandleFunc("GET /evaluations/{evaluation_id}", s.staticPage("evaluations.html"))
	mux.HandleFunc("GET /assets/evaluations.js",
		s.staticPage(filepath.Join("assets", "evaluations.js")))
	// F06/T050: the approval queue/detail page. Static, compile-time path.
	mux.HandleFunc("GET /approvals", s.staticPage("approvals.html"))
	mux.HandleFunc("GET /assets/app.js", s.staticPage(filepath.Join("assets", "app.js")))
	mux.HandleFunc("GET /assets/editor.js", s.staticPage(filepath.Join("assets", "editor.js")))
	mux.HandleFunc("GET /assets/debug.js", s.staticPage(filepath.Join("assets", "debug.js")))
	mux.HandleFunc("GET /assets/attachments.js", s.staticPage(filepath.Join("assets", "attachments.js")))
	mux.HandleFunc("GET /assets/tools.js", s.staticPage(filepath.Join("assets", "tools.js")))
	mux.HandleFunc("GET /knowledge", s.staticPage("knowledge.html"))
	mux.HandleFunc("GET /assets/knowledge.js", s.staticPage(filepath.Join("assets", "knowledge.js")))
	mux.HandleFunc("GET /assets/models.js", s.staticPage(filepath.Join("assets", "models.js")))
	mux.HandleFunc("GET /assets/runs.js", s.staticPage(filepath.Join("assets", "runs.js")))
	// F09/T054: the personal-memory candidate page and its logic.
	mux.HandleFunc("GET /memories", s.staticPage("memories.html"))
	mux.HandleFunc("GET /assets/memories.js", s.staticPage(filepath.Join("assets", "memories.js")))
	// F06/T050: the approval page helpers (proposal rendering, no defaults).
	mux.HandleFunc("GET /assets/approvals.js", s.staticPage(filepath.Join("assets", "approvals.js")))
	// F06/T044: the shared temporary model preview helpers loaded by /debug and /runs.
	mux.HandleFunc("GET /assets/stream-preview.js", s.staticPage(filepath.Join("assets", "stream-preview.js")))
	// F04A/T040: the committed Vue Flow canvas bundle. The page never needs npm;
	// the bundle and its stylesheet are static build artifacts checked into the tree.
	mux.HandleFunc("GET /assets/canvas.js", s.staticPage(filepath.Join("assets", "canvas.js")))
	mux.HandleFunc("GET /assets/canvas.css", s.staticPage(filepath.Join("assets", "canvas.css")))
}

type loginRequest struct {
	Login    string `json:"login"`
	Password string `json:"password"`
}

type sessionResponse struct {
	PrincipalID string `json:"principal_id"`
	CSRFToken   string `json:"csrf_token"`
	ExpiresAt   string `json:"expires_at"`
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	var request loginRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}

	// Throttle before spending an argon2id verification. The key is
	// (lowercased login, peer address); a tripped key is refused even when the
	// password is correct, so the refusal itself carries no signal about the
	// password.
	throttleKey := loginKeyFor(request.Login, r.RemoteAddr)
	if blocked, retryAfter := s.limiter.blocked(throttleKey); blocked {
		w.Header().Set("Retry-After", strconv.Itoa(int(retryAfter/time.Second)))
		writeError(w, r, http.StatusTooManyRequests, "rate_limited",
			"too many failed sign-in attempts; try again later")
		return
	}

	session, err := s.service.Login(r.Context(), request.Login, request.Password)
	switch {
	case errors.Is(err, identity.ErrInvalidCredentials):
		s.limiter.fail(throttleKey)
		// Same answer for an unknown login and a wrong password.
		writeError(w, r, http.StatusUnauthorized, "invalid_credentials", "login or password is incorrect")
		return
	case errors.Is(err, identity.ErrPrincipalDisabled):
		writeError(w, r, http.StatusForbidden, "principal_disabled", "this account is disabled")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	s.limiter.succeed(throttleKey)
	http.SetCookie(w, newSessionCookie(session.Token, s.secureCookie))
	writeJSON(w, http.StatusOK, sessionResponse{
		PrincipalID: session.PrincipalID,
		CSRFToken:   session.CSRFToken,
		ExpiresAt:   session.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

// identityView is the contract's IdentityView: exactly these five fields, with
// csrf_token null for a credential that does not use browser CSRF.
type identityView struct {
	PrincipalID string   `json:"principal_id"`
	Kind        string   `json:"kind"`
	Roles       []string `json:"roles"`
	CSRFToken   *string  `json:"csrf_token"`
	ExpiresAt   string   `json:"expires_at"`
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	writeJSON(w, http.StatusOK, identityViewFor(caller))
}

// identityViewFor projects an identity onto the contract's IdentityView.
//
// csrf_token is the plaintext token only for a session cookie, where it is
// recomputed from the presented cookie inside Authenticate, so a page refresh
// restores a working identity without any client-side memory and without the
// plaintext ever being stored server-side. Any other credential (an API key,
// from T020) reports null: it is not attached by a browser and has no CSRF
// token to hand out.
func identityViewFor(caller *identity.Identity) identityView {
	var csrfToken *string
	if caller.Method == identity.AuthMethodSessionCookie && caller.CSRFToken != "" {
		token := caller.CSRFToken
		csrfToken = &token
	}
	roles := caller.Roles
	if roles == nil {
		// The contract fixes roles as an array; JSON null would be a different
		// type. A service principal simply has no roles.
		roles = []string{}
	}
	return identityView{
		PrincipalID: caller.PrincipalID,
		Kind:        caller.Kind,
		Roles:       roles,
		CSRFToken:   csrfToken,
		ExpiresAt:   caller.ExpiresAt.UTC().Format(time.RFC3339),
	}
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	// A forged logout is still a state change (it ends someone's session), so
	// it needs the same CSRF proof as any other write. authorize() writes 403
	// csrf_invalid and leaves the session untouched when the proof is missing.
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}

	// Only a browser session has a server-side row to revoke. An API key is not
	// a session: it has no CSRF token and its lifecycle is the admin revoke
	// operation, so this endpoint refuses a bearer caller instead of clearing a
	// cookie that was never used and pretending it revoked a credential.
	if caller.Method != identity.AuthMethodSessionCookie || caller.SessionHash == "" {
		writeError(w, r, http.StatusUnauthorized, "unauthenticated",
			"signing out requires a browser session; revoke an API key through the admin API")
		return
	}
	if _, err := s.service.Logout(r.Context(), caller.SessionHash, caller.PrincipalID); err != nil {
		s.internalError(w, r, err)
		return
	}
	http.SetCookie(w, clearedSessionCookie(s.secureCookie))
	// No body, but still auth data: the response must not be cached.
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNoContent)
}

type inviteRequest struct {
	Login       string   `json:"login"`
	DisplayName string   `json:"display_name"`
	Roles       []string `json:"roles"`
}

type invitationResponse struct {
	PrincipalID     string `json:"principal_id"`
	ActivationToken string `json:"activation_token"`
	ExpiresAt       string `json:"expires_at"`
}

func (s *Server) handleInviteUser(w http.ResponseWriter, r *http.Request) {
	// Invitation is a state-changing admin endpoint: one helper enforces the
	// session, the CSRF proof and the admin role before anything is read or
	// written.
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}

	var request inviteRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}

	invitation, err := s.service.InviteUser(
		r.Context(), caller.PrincipalID, request.Login, request.DisplayName, request.Roles,
	)
	switch {
	case errors.Is(err, identity.ErrLoginTaken):
		writeError(w, r, http.StatusConflict, "login_taken", "that login is already taken")
		return
	case errors.Is(err, identity.ErrValidation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity,
			"validation_failed", err.Error(), validationDetails(err))
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	writeJSON(w, http.StatusCreated, invitationResponse{
		PrincipalID:     invitation.PrincipalID,
		ActivationToken: invitation.Token,
		ExpiresAt:       invitation.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

type activateRequest struct {
	ActivationToken string `json:"activation_token"`
	Password        string `json:"password"`
}

type acceptedResponse struct {
	ID    string `json:"id"`
	State string `json:"state"`
}

func (s *Server) handleActivate(w http.ResponseWriter, r *http.Request) {
	var request activateRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}

	principalID, err := s.service.Activate(r.Context(), request.ActivationToken, request.Password)
	switch {
	case errors.Is(err, identity.ErrValidation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity,
			"validation_failed", err.Error(), validationDetails(err))
		return
	case errors.Is(err, identity.ErrActivationConsumed):
		writeError(w, r, http.StatusConflict, "activation_consumed",
			"that activation token was already used")
		return
	case errors.Is(err, identity.ErrActivationExpired):
		writeError(w, r, http.StatusConflict, "activation_expired",
			"that activation token has expired")
		return
	case errors.Is(err, identity.ErrActivationInvalid):
		writeError(w, r, http.StatusConflict, "activation_invalid",
			"that activation token is not recognised")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	writeJSON(w, http.StatusOK, acceptedResponse{ID: principalID, State: "activated"})
}

func validationDetails(err error) map[string]any {
	var identityValidation *identity.ValidationError
	if errors.As(err, &identityValidation) && identityValidation.Field != "" {
		return map[string]any{"field": identityValidation.Field}
	}
	var policyValidation *policy.ValidationError
	if errors.As(err, &policyValidation) && policyValidation.Field != "" {
		return map[string]any{"field": policyValidation.Field}
	}
	var catalogValidation *catalog.ValidationError
	if errors.As(err, &catalogValidation) && catalogValidation.Field != "" {
		return map[string]any{"field": catalogValidation.Field}
	}
	return nil
}

func (s *Server) internalError(w http.ResponseWriter, r *http.Request, err error) {
	s.logger.Printf("request_id=%s internal error: %v", RequestID(r.Context()), err)
	writeError(w, r, http.StatusInternalServerError, "internal_error", "internal error")
}

// staticPage serves one known file from ORCHARA_WEB_DIR. The relative path is a
// compile-time constant, so no request input can escape the web directory.
func (s *Server) staticPage(relative string) http.HandlerFunc {
	path := filepath.Join(s.webDir, relative)
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		http.ServeFile(w, r, path)
	}
}

type nullWriter struct{}

func (nullWriter) Write(body []byte) (int, error) { return len(body), nil }
