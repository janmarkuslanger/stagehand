package storage

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/janmarkuslanger/stagehand/ports"
)

// FilesystemStorage stores artifacts on the local filesystem under a root directory.
type FilesystemStorage struct {
	root string
}

// NewFilesystemStorage creates a FilesystemStorage rooted at the given directory.
func NewFilesystemStorage(root string) *FilesystemStorage {
	return &FilesystemStorage{root: root}
}

// Write creates or overwrites a file at path relative to the storage root.
func (s *FilesystemStorage) Write(_ context.Context, path string, content []byte) error {
	fullPath := filepath.Join(s.root, path)
	if err := os.MkdirAll(filepath.Dir(fullPath), 0755); err != nil {
		return fmt.Errorf("filesystem storage: create parent directories for %s: %w", path, err)
	}
	if err := os.WriteFile(fullPath, content, 0644); err != nil {
		return fmt.Errorf("filesystem storage: write %s: %w", path, err)
	}
	return nil
}

// Read returns the content of a file at path relative to the storage root.
func (s *FilesystemStorage) Read(_ context.Context, path string) ([]byte, error) {
	fullPath := filepath.Join(s.root, path)
	data, err := os.ReadFile(fullPath)
	if err != nil {
		return nil, fmt.Errorf("filesystem storage: read %s: %w", path, err)
	}
	return data, nil
}

// List returns paths matching the given glob pattern, relative to the storage root.
func (s *FilesystemStorage) List(_ context.Context, pattern string) ([]string, error) {
	fullPattern := filepath.Join(s.root, pattern)
	matches, err := filepath.Glob(fullPattern)
	if err != nil {
		return nil, fmt.Errorf("filesystem storage: glob %s: %w", pattern, err)
	}
	relative := make([]string, len(matches))
	for i, match := range matches {
		rel, err := filepath.Rel(s.root, match)
		if err != nil {
			return nil, fmt.Errorf("filesystem storage: relative path for %s: %w", match, err)
		}
		relative[i] = rel
	}
	return relative, nil
}

// Compile-time check that FilesystemStorage satisfies ports.ArtifactStorage.
var _ ports.ArtifactStorage = (*FilesystemStorage)(nil)
