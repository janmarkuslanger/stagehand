package ports

import "context"

// ArtifactStorage reads and writes task output files.
type ArtifactStorage interface {
	Write(ctx context.Context, path string, content []byte) error
	Read(ctx context.Context, path string) ([]byte, error)
	List(ctx context.Context, pattern string) ([]string, error)
}
