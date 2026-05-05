package storage

import (
	"context"
	"testing"
)

func TestFilesystemStorage_WriteAndRead(t *testing.T) {
	directory := t.TempDir()
	s := NewFilesystemStorage(directory)

	content := []byte("hello world")
	if err := s.Write(context.Background(), "test.txt", content); err != nil {
		t.Fatalf("Write: %v", err)
	}

	got, err := s.Read(context.Background(), "test.txt")
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if string(got) != string(content) {
		t.Errorf("expected %q, got %q", content, got)
	}
}

func TestFilesystemStorage_WriteCreatesSubdirectories(t *testing.T) {
	directory := t.TempDir()
	s := NewFilesystemStorage(directory)

	if err := s.Write(context.Background(), "subdir/nested/file.txt", []byte("data")); err != nil {
		t.Fatalf("Write with subdirectory: %v", err)
	}

	_, err := s.Read(context.Background(), "subdir/nested/file.txt")
	if err != nil {
		t.Fatalf("Read after subdirectory write: %v", err)
	}
}

func TestFilesystemStorage_ReadMissingFile(t *testing.T) {
	directory := t.TempDir()
	s := NewFilesystemStorage(directory)

	_, err := s.Read(context.Background(), "missing.txt")
	if err == nil {
		t.Fatal("expected error for missing file, got nil")
	}
}

func TestFilesystemStorage_OverwritesExistingFile(t *testing.T) {
	directory := t.TempDir()
	s := NewFilesystemStorage(directory)

	if err := s.Write(context.Background(), "file.txt", []byte("original")); err != nil {
		t.Fatalf("first Write: %v", err)
	}
	if err := s.Write(context.Background(), "file.txt", []byte("updated")); err != nil {
		t.Fatalf("second Write: %v", err)
	}

	got, err := s.Read(context.Background(), "file.txt")
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if string(got) != "updated" {
		t.Errorf("expected %q after overwrite, got %q", "updated", string(got))
	}
}

func TestFilesystemStorage_List(t *testing.T) {
	directory := t.TempDir()
	s := NewFilesystemStorage(directory)

	for _, name := range []string{"a.txt", "b.txt", "c.md"} {
		if err := s.Write(context.Background(), name, []byte("x")); err != nil {
			t.Fatalf("Write %s: %v", name, err)
		}
	}

	matches, err := s.List(context.Background(), "*.txt")
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(matches) != 2 {
		t.Errorf("expected 2 .txt matches, got %d: %v", len(matches), matches)
	}
}

func TestFilesystemStorage_ListEmptyResult(t *testing.T) {
	directory := t.TempDir()
	s := NewFilesystemStorage(directory)

	matches, err := s.List(context.Background(), "*.txt")
	if err != nil {
		t.Fatalf("List on empty directory: %v", err)
	}
	if len(matches) != 0 {
		t.Errorf("expected 0 matches, got %d", len(matches))
	}
}
