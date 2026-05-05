package ports

import "context"

// SecretProvider resolves named secrets at runtime.
type SecretProvider interface {
	Get(ctx context.Context, key string) (string, error)
}
