package main

import (
	"context"
	"log"
	"net/http"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
	"github.com/codywiki/lodia/apps/api-go/internal/httpapi"
	"github.com/codywiki/lodia/apps/api-go/internal/jobqueue"
	"github.com/codywiki/lodia/apps/api-go/internal/objectstore"
	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

func main() {
	ctx := context.Background()
	cfg := config.FromEnv()
	db, err := store.Open(ctx, cfg.MySQLDSN)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	objects, err := objectstore.New(cfg)
	if err != nil {
		log.Fatal(err)
	}
	queue := jobqueue.NewRedis(cfg.RedisURL)
	defer queue.Close()

	api := httpapi.New(cfg, db, objects, queue)
	log.Printf("lodia go api listening on %s", cfg.HTTPAddr)
	if err := http.ListenAndServe(cfg.HTTPAddr, api.Router()); err != nil {
		log.Fatal(err)
	}
}
