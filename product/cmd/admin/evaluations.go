package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/config"
	"orchara/platform/internal/uuid"
)

// runEvaluations is the administrative control interface for F00B/T026
// evaluations. Enqueue is the only operation; it creates the evaluation row and
// its application_evaluate background job in one transaction. There is no
// "run an evaluation now" verb, and --kind is a closed two-value choice.
func runEvaluations(args []string) error {
	if len(args) == 0 {
		return usageErrorf("missing evaluations command; expected 'enqueue'")
	}
	switch args[0] {
	case "enqueue":
		return runEvaluationsEnqueue(args[1:])
	case "help", "-h", "--help":
		fmt.Fprint(os.Stdout, evaluationsUsage)
		return nil
	default:
		return usageErrorf("unknown evaluations command %q; expected 'enqueue'", args[0])
	}
}

const evaluationsUsage = `usage: orchara-admin evaluations enqueue --kind model|application --owner-login LOGIN \
    --dataset-artifact-id UUID [--model-profile-version-id UUID] [--snapshot-id UUID]

One transaction writes:
  platform.evaluation_jobs   (state=queued, owner, dataset artifact)
  platform.background_jobs   (kind=application_evaluate, target=evaluation id,
                              input_artifact_id=dataset artifact, dedup=evaluation id)

  --kind=model        requires --model-profile-version-id; the report binds to the
                      candidate's manifest_sha256. This is the kind T026 can
                      execute end to end.
  --kind=application  requires --snapshot-id; T027 creates execution_snapshots and
                      the FK. T026 accepts the row structurally but cannot execute
                      it yet (honest partial).

Shape errors exit 2 and write nothing. A refusal decided against the database
(unknown login, missing candidate, missing dataset artifact, ...) exits 1 and
writes nothing. Success prints {"id","kind","state","background_job_id"}.
`

func runEvaluationsEnqueue(args []string) error {
	flags := flag.NewFlagSet("evaluations enqueue", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	kindFlag := flags.String("kind", "", "model or application")
	ownerLogin := flags.String("owner-login", "", "login of the evaluation owner")
	datasetID := flags.String("dataset-artifact-id", "", "sealed dataset artifact")
	modelID := flags.String("model-profile-version-id", "", "candidate (kind=model only)")
	snapshotID := flags.String("snapshot-id", "", "execution snapshot (kind=application only)")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if flags.NArg() > 0 {
		return usageErrorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}

	kind := strings.TrimSpace(*kindFlag)
	if kind == "" {
		return usageErrorf("--kind is required")
	}
	if kind != "model" && kind != "application" {
		return usageErrorf("--kind must be model or application")
	}
	login := strings.TrimSpace(*ownerLogin)
	if login == "" {
		return usageErrorf("--owner-login is required")
	}
	if len(login) > 200 || strings.ContainsAny(login, "\r\n\x00") {
		return usageErrorf("--owner-login is not a valid login")
	}
	dataset := strings.TrimSpace(*datasetID)
	if dataset == "" {
		return usageErrorf("--dataset-artifact-id is required")
	}
	if !uuid.Valid(dataset) {
		return usageErrorf("--dataset-artifact-id must be a UUID")
	}
	model := strings.TrimSpace(*modelID)
	snapshot := strings.TrimSpace(*snapshotID)
	switch kind {
	case "model":
		if snapshot != "" {
			return usageErrorf("--snapshot-id is only valid with --kind application")
		}
		if model == "" {
			return usageErrorf("--model-profile-version-id is required with --kind model")
		}
		if !uuid.Valid(model) {
			return usageErrorf("--model-profile-version-id must be a UUID")
		}
	case "application":
		if model != "" {
			return usageErrorf("--model-profile-version-id is only valid with --kind model")
		}
		if snapshot == "" {
			return usageErrorf("--snapshot-id is required with --kind application")
		}
		if !uuid.Valid(snapshot) {
			return usageErrorf("--snapshot-id must be a UUID")
		}
	}

	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		// The DSN is not echoed: it carries the password.
		return errors.New("cannot reach the database")
	}

	evaluationID, jobID, err := enqueueEvaluation(ctx, db, evaluationEnqueue{
		Kind:                  kind,
		OwnerLogin:            login,
		DatasetArtifactID:     dataset,
		ModelProfileVersionID: model,
		SnapshotID:            snapshot,
	})
	if err != nil {
		return err
	}
	payload := struct {
		ID              string `json:"id"`
		Kind            string `json:"kind"`
		State           string `json:"state"`
		BackgroundJobID string `json:"background_job_id"`
	}{ID: evaluationID, Kind: kind, State: "queued", BackgroundJobID: jobID}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	fmt.Fprintln(os.Stdout, string(encoded))
	return nil
}

type evaluationEnqueue struct {
	Kind                  string
	OwnerLogin            string
	DatasetArtifactID     string
	ModelProfileVersionID string
	SnapshotID            string
}

// enqueueEvaluation writes the evaluation row and the background job in one
// transaction. The background job is created first because the evaluation
// references its generated id; both roll back together on any failure.
func enqueueEvaluation(
	ctx context.Context,
	db *sql.DB,
	request evaluationEnqueue,
) (evaluationID, jobID string, err error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return "", "", errors.New("cannot start the evaluation transaction")
	}
	defer func() { _ = tx.Rollback() }()

	var ownerID string
	err = tx.QueryRowContext(ctx, `
		SELECT p.id::text
		  FROM platform.principals AS p
		  JOIN platform.users AS u ON u.principal_id = p.id
		 WHERE u.login = $1`, request.OwnerLogin).Scan(&ownerID)
	if errors.Is(err, sql.ErrNoRows) {
		return "", "", fmt.Errorf("no user with login %q", request.OwnerLogin)
	}
	if err != nil {
		return "", "", errors.New("cannot resolve the evaluation owner")
	}

	if request.Kind == "model" {
		var exists bool
		if err := tx.QueryRowContext(ctx, `
			SELECT EXISTS (SELECT 1 FROM platform.model_profile_versions WHERE id = $1::uuid)`,
			request.ModelProfileVersionID).Scan(&exists); err != nil {
			return "", "", errors.New("cannot look up the model profile version")
		}
		if !exists {
			return "", "", fmt.Errorf("model profile version %s does not exist", request.ModelProfileVersionID)
		}
	}

	evaluationID = uuid.NewString()
	job, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:            background.KindApplicationEvaluate,
		TargetID:        evaluationID,
		OwnerID:         ownerID,
		DedupKey:        evaluationID,
		InputArtifactID: request.DatasetArtifactID,
	})
	if err != nil {
		return "", "", err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_jobs
		       (id, kind, owner_id, background_job_id, snapshot_id,
		        model_profile_version_id, dataset_artifact_id, state)
		VALUES ($1::uuid, $2, $3::uuid, $4::uuid, NULLIF($5, '')::uuid,
		        NULLIF($6, '')::uuid, $7::uuid, 'queued')`,
		evaluationID, request.Kind, ownerID, job.ID, request.SnapshotID,
		request.ModelProfileVersionID, request.DatasetArtifactID,
	); err != nil {
		return "", "", fmt.Errorf("creating the evaluation: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return "", "", errors.New("cannot commit the evaluation transaction")
	}
	return evaluationID, job.ID, nil
}
