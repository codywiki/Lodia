package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"os"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
	"github.com/codywiki/lodia/apps/api-go/internal/jobqueue"
	"github.com/codywiki/lodia/apps/api-go/internal/objectstore"
	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

type Worker struct {
	cfg       config.Config
	db        *store.DB
	queue     *jobqueue.RedisQueue
	processor Processor
	workerID  string
}

func NewWorker(cfg config.Config, db *store.DB, objects objectstore.Store, queue *jobqueue.RedisQueue) *Worker {
	host, _ := os.Hostname()
	return &Worker{
		cfg:       cfg,
		db:        db,
		queue:     queue,
		processor: NewProcessor(cfg, db, objects),
		workerID:  host + ":go-worker",
	}
}

func (w *Worker) Run(ctx context.Context) error {
	log.Printf("lodia worker started queue=%s worker_id=%s", w.cfg.WorkerQueue, w.workerID)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		jobID, err := w.queue.Pop(ctx, w.cfg.WorkerQueue, 5*time.Second)
		if err != nil {
			if errors.Is(err, context.Canceled) {
				return err
			}
			log.Printf("redis pop failed: %v", err)
			time.Sleep(time.Second)
			continue
		}
		if jobID == "" {
			continue
		}
		if err := w.handleJob(ctx, jobID); err != nil {
			log.Printf("job %s failed: %v", jobID, err)
		}
	}
}

func (w *Worker) handleJob(ctx context.Context, jobID string) error {
	job, claimed, err := w.db.MarkJobRunning(ctx, jobID, w.workerID)
	if err != nil {
		return err
	}
	if !claimed {
		return nil
	}
	var payload struct {
		SubmissionID string `json:"submission_id"`
	}
	if err := json.Unmarshal([]byte(job.PayloadJSON), &payload); err != nil {
		_, _ = w.db.MarkJobFailed(ctx, job.ID, err.Error())
		return err
	}
	switch job.JobType {
	case "process_submission":
		if payload.SubmissionID == "" {
			payload.SubmissionID = job.SubmissionID
		}
		_, err = w.processor.ProcessSubmission(ctx, payload.SubmissionID)
	default:
		err = errors.New("unknown_job_type")
	}
	if err != nil {
		shouldRetry, markErr := w.db.MarkJobFailed(ctx, job.ID, err.Error())
		if markErr != nil {
			return markErr
		}
		if shouldRetry {
			_ = w.queue.Push(ctx, job.QueueName, job.ID)
		}
		return err
	}
	return w.db.MarkJobDone(ctx, job.ID)
}
