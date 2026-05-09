package jobqueue

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

type RedisQueue struct {
	client *redis.Client
}

func NewRedis(url string) *RedisQueue {
	opts, err := redis.ParseURL(url)
	if err != nil {
		opts = &redis.Options{Addr: "127.0.0.1:6379"}
	}
	return &RedisQueue{client: redis.NewClient(opts)}
}

func (q *RedisQueue) Close() error {
	return q.client.Close()
}

func (q *RedisQueue) Push(ctx context.Context, queueName string, jobID string) error {
	return q.client.LPush(ctx, key(queueName), jobID).Err()
}

func (q *RedisQueue) Pop(ctx context.Context, queueName string, timeout time.Duration) (string, error) {
	values, err := q.client.BRPop(ctx, timeout, key(queueName)).Result()
	if err == redis.Nil {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	if len(values) < 2 {
		return "", nil
	}
	return values[1], nil
}

func (q *RedisQueue) Health(ctx context.Context) map[string]any {
	err := q.client.Ping(ctx).Err()
	return map[string]any{"ok": err == nil, "backend": "redis"}
}

func key(queueName string) string {
	return "lodia:queue:" + queueName
}
