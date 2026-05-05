package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
	"github.com/janmarkuslanger/stagehand/ports"
)

// memoryStorage is a thread-safe in-memory implementation of ports.ArtifactStorage
// used only in tests to avoid importing another adapter package.
type memoryStorage struct {
	mu    sync.RWMutex
	files map[string][]byte
}

func newMemoryStorage() *memoryStorage {
	return &memoryStorage{files: make(map[string][]byte)}
}

func (s *memoryStorage) Write(_ context.Context, filePath string, content []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.files[filePath] = append([]byte(nil), content...)
	return nil
}

func (s *memoryStorage) Read(_ context.Context, filePath string) ([]byte, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	data, ok := s.files[filePath]
	if !ok {
		return nil, fmt.Errorf("memory storage: not found: %s", filePath)
	}
	return append([]byte(nil), data...), nil
}

func (s *memoryStorage) List(_ context.Context, pattern string) ([]string, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var matches []string
	for filePath := range s.files {
		matched, err := path.Match(pattern, filePath)
		if err != nil {
			return nil, fmt.Errorf("memory storage: invalid pattern %q: %w", pattern, err)
		}
		if matched {
			matches = append(matches, filePath)
		}
	}
	sort.Strings(matches)
	return matches, nil
}

var _ ports.ArtifactStorage = (*memoryStorage)(nil)

// anthropicResponse is a minimal Anthropic Messages API response for test fixtures.
type anthropicResponse struct {
	ID         string                   `json:"id"`
	Type       string                   `json:"type"`
	Role       string                   `json:"role"`
	Model      string                   `json:"model"`
	Content    []map[string]interface{} `json:"content"`
	StopReason string                   `json:"stop_reason"`
	Usage      map[string]int           `json:"usage"`
}

func writeJSONResponse(w http.ResponseWriter, body interface{}) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(body); err != nil {
		http.Error(w, "encode error", http.StatusInternalServerError)
	}
}

func endTurnResponse(text string) anthropicResponse {
	return anthropicResponse{
		ID:    "msg_test",
		Type:  "message",
		Role:  "assistant",
		Model: defaultModel,
		Content: []map[string]interface{}{
			{"type": "text", "text": text},
		},
		StopReason: "end_turn",
		Usage:      map[string]int{"input_tokens": 10, "output_tokens": 5},
	}
}

func toolUseResponse(toolID, toolName string, input interface{}) anthropicResponse {
	inputBytes, _ := json.Marshal(input)
	return anthropicResponse{
		ID:    "msg_test",
		Type:  "message",
		Role:  "assistant",
		Model: defaultModel,
		Content: []map[string]interface{}{
			{
				"type":  "tool_use",
				"id":    toolID,
				"name":  toolName,
				"input": json.RawMessage(inputBytes),
			},
		},
		StopReason: "tool_use",
		Usage:      map[string]int{"input_tokens": 10, "output_tokens": 20},
	}
}

func newTestExecutor(t *testing.T, handler http.HandlerFunc) (*ClaudeExecutor, *memoryStorage) {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)

	client := anthropic.NewClient(
		option.WithBaseURL(server.URL),
		option.WithAPIKey("test-key"),
	)
	artifactStorage := newMemoryStorage()
	return NewClaudeExecutor(&client, artifactStorage), artifactStorage
}

// extractToolResultText parses the Anthropic Messages API request body and returns
// the text content of the first tool_result block in the last user message.
func extractToolResultText(r *http.Request) string {
	var body map[string]interface{}
	_ = json.NewDecoder(r.Body).Decode(&body)
	messages, _ := body["messages"].([]interface{})
	for i := len(messages) - 1; i >= 0; i-- {
		m, _ := messages[i].(map[string]interface{})
		if m["role"] != "user" {
			continue
		}
		content, _ := m["content"].([]interface{})
		for _, c := range content {
			block, _ := c.(map[string]interface{})
			if block["type"] != "tool_result" {
				continue
			}
			// Content is [{type: text, text: "..."}]
			blocks, _ := block["content"].([]interface{})
			for _, cb := range blocks {
				textBlock, _ := cb.(map[string]interface{})
				if textBlock["type"] == "text" {
					if s, ok := textBlock["text"].(string); ok {
						return s
					}
				}
			}
		}
	}
	return ""
}

func TestClaudeExecutor_Execute_EndTurnResponse(t *testing.T) {
	executor, _ := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(w, endTurnResponse("The task is done."))
	})

	result, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "task1",
		Prompt: "Do something.",
		Model:  defaultModel,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Output != "The task is done." {
		t.Errorf("expected output %q, got %q", "The task is done.", result.Output)
	}
	if len(result.Files) != 0 {
		t.Errorf("expected no files, got %v", result.Files)
	}
}

func TestClaudeExecutor_Execute_WriteFileTool(t *testing.T) {
	callCount := 0
	executor, artifactStorage := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount == 1 {
			writeJSONResponse(w, toolUseResponse("toolu_1", "write_file", map[string]string{
				"path":    "output.md",
				"content": "# Hello",
			}))
			return
		}
		writeJSONResponse(w, endTurnResponse("File written successfully."))
	})

	result, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "mytask",
		Prompt: "Write a file.",
		Model:  defaultModel,
		Tools:  []string{"write_file"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Output != "File written successfully." {
		t.Errorf("expected output %q, got %q", "File written successfully.", result.Output)
	}
	if len(result.Files) != 1 || result.Files[0] != "mytask/output.md" {
		t.Errorf("expected Files [mytask/output.md], got %v", result.Files)
	}

	content, readErr := artifactStorage.Read(context.Background(), "mytask/output.md")
	if readErr != nil {
		t.Fatalf("storage.Read: %v", readErr)
	}
	if string(content) != "# Hello" {
		t.Errorf("expected file content %q, got %q", "# Hello", string(content))
	}
	if callCount != 2 {
		t.Errorf("expected 2 API calls, got %d", callCount)
	}
}

func TestClaudeExecutor_Execute_ReadFileTool(t *testing.T) {
	callCount := 0
	var capturedToolResult string

	executor, artifactStorage := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount == 1 {
			writeJSONResponse(w, toolUseResponse("toolu_1", "read_file", map[string]string{
				"path": "source.txt",
			}))
			return
		}
		capturedToolResult = extractToolResultText(r)
		writeJSONResponse(w, endTurnResponse("Read done."))
	})

	_ = artifactStorage.Write(context.Background(), "readtask/source.txt", []byte("file content here"))

	_, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "readtask",
		Prompt: "Read the file.",
		Model:  defaultModel,
		Tools:  []string{"read_file"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if capturedToolResult != "file content here" {
		t.Errorf("expected tool result %q, got %q", "file content here", capturedToolResult)
	}
}

func TestClaudeExecutor_Execute_ListFilesTool(t *testing.T) {
	callCount := 0
	var capturedToolResult string

	executor, artifactStorage := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount == 1 {
			writeJSONResponse(w, toolUseResponse("toolu_1", "list_files", map[string]string{
				"pattern": "*.md",
			}))
			return
		}
		capturedToolResult = extractToolResultText(r)
		writeJSONResponse(w, endTurnResponse("Listed."))
	})

	_ = artifactStorage.Write(context.Background(), "listtask/a.md", []byte("a"))
	_ = artifactStorage.Write(context.Background(), "listtask/b.md", []byte("b"))

	_, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "listtask",
		Prompt: "List files.",
		Model:  defaultModel,
		Tools:  []string{"list_files"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(capturedToolResult, "listtask/a.md") || !strings.Contains(capturedToolResult, "listtask/b.md") {
		t.Errorf("expected both files in tool result, got %q", capturedToolResult)
	}
}

func TestClaudeExecutor_Execute_UnknownToolReturnsError(t *testing.T) {
	callCount := 0
	executor, _ := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount == 1 {
			writeJSONResponse(w, toolUseResponse("toolu_1", "unknown_tool", map[string]string{}))
			return
		}
		writeJSONResponse(w, endTurnResponse("Handled error."))
	})

	_, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "errtask",
		Prompt: "Do something.",
		Model:  defaultModel,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestClaudeExecutor_Execute_APIError(t *testing.T) {
	executor, _ := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"type":"error","error":{"type":"api_error","message":"internal error"}}`, http.StatusInternalServerError)
	})

	_, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "errtask",
		Prompt: "Do something.",
		Model:  defaultModel,
	})
	if err == nil {
		t.Fatal("expected error from API failure, got nil")
	}
}

func TestClaudeExecutor_Execute_MaxStepsExceeded(t *testing.T) {
	executor, _ := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(w, toolUseResponse("toolu_1", "write_file", map[string]string{
			"path":    "out.txt",
			"content": "x",
		}))
	})

	_, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "steptask",
		Prompt: "Loop forever.",
		Model:  defaultModel,
		Tools:  []string{"write_file"},
	})
	if err == nil {
		t.Fatal("expected error when max steps exceeded, got nil")
	}
	if !strings.Contains(err.Error(), "did not complete") {
		t.Errorf("expected 'did not complete' in error, got %q", err.Error())
	}
}

func TestClaudeExecutor_Execute_DefaultModelUsedWhenEmpty(t *testing.T) {
	var capturedModel string
	executor, _ := newTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if m, ok := body["model"].(string); ok {
			capturedModel = m
		}
		writeJSONResponse(w, endTurnResponse("done"))
	})

	_, err := executor.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "t",
		Prompt: "p",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if capturedModel != defaultModel {
		t.Errorf("expected default model %q, got %q", defaultModel, capturedModel)
	}
}
