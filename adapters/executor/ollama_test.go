package executor

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/janmarkuslanger/stagehand/ports"
)

// openAIResponse is a minimal OpenAI-compatible chat completions response.
type openAIResponse struct {
	ID      string         `json:"id"`
	Object  string         `json:"object"`
	Model   string         `json:"model"`
	Choices []openAIChoice `json:"choices"`
	Usage   map[string]int `json:"usage"`
}

type openAIChoice struct {
	Index        int           `json:"index"`
	Message      openAIMessage `json:"message"`
	FinishReason string        `json:"finish_reason"`
}

type openAIMessage struct {
	Role      string          `json:"role"`
	Content   string          `json:"content"`
	ToolCalls []openAIToolCall `json:"tool_calls,omitempty"`
}

type openAIToolCall struct {
	ID       string             `json:"id"`
	Type     string             `json:"type"`
	Function openAIToolCallFunc `json:"function"`
}

type openAIToolCallFunc struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

func ollamaEndTurnResponse(text string) openAIResponse {
	return openAIResponse{
		ID:     "chatcmpl-test",
		Object: "chat.completion",
		Model:  ollamaDefaultModel,
		Choices: []openAIChoice{
			{Message: openAIMessage{Role: "assistant", Content: text}, FinishReason: "stop"},
		},
		Usage: map[string]int{"prompt_tokens": 10, "completion_tokens": 5},
	}
}

func ollamaToolUseResponse(toolID, toolName string, input interface{}) openAIResponse {
	inputBytes, _ := json.Marshal(input)
	return openAIResponse{
		ID:     "chatcmpl-test",
		Object: "chat.completion",
		Model:  ollamaDefaultModel,
		Choices: []openAIChoice{
			{
				Message: openAIMessage{
					Role: "assistant",
					ToolCalls: []openAIToolCall{
						{
							ID:   toolID,
							Type: "function",
							Function: openAIToolCallFunc{
								Name:      toolName,
								Arguments: string(inputBytes),
							},
						},
					},
				},
				FinishReason: "tool_calls",
			},
		},
		Usage: map[string]int{"prompt_tokens": 10, "completion_tokens": 20},
	}
}

func newOllamaTestExecutor(t *testing.T, handler http.HandlerFunc) (*OllamaExecutor, *memoryStorage) {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	artifactStorage := newMemoryStorage()
	return NewOllamaExecutor(server.URL, artifactStorage), artifactStorage
}

// extractOllamaToolResult returns the content of the first tool message in the request body.
func extractOllamaToolResult(body map[string]interface{}) string {
	messages, _ := body["messages"].([]interface{})
	for i := len(messages) - 1; i >= 0; i-- {
		m, _ := messages[i].(map[string]interface{})
		if m["role"] == "tool" {
			if content, ok := m["content"].(string); ok {
				return content
			}
		}
	}
	return ""
}

func TestOllamaExecutor_Execute_EndTurnResponse(t *testing.T) {
	ex, _ := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(w, ollamaEndTurnResponse("Task complete."))
	})

	result, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "task1",
		Prompt: "Do something.",
		Model:  ollamaDefaultModel,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Output != "Task complete." {
		t.Errorf("expected output %q, got %q", "Task complete.", result.Output)
	}
	if len(result.Files) != 0 {
		t.Errorf("expected no files, got %v", result.Files)
	}
}

func TestOllamaExecutor_Execute_SystemPromptSentFirst(t *testing.T) {
	var capturedMessages []map[string]interface{}
	ex, _ := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if msgs, ok := body["messages"].([]interface{}); ok {
			for _, m := range msgs {
				if msg, ok := m.(map[string]interface{}); ok {
					capturedMessages = append(capturedMessages, msg)
				}
			}
		}
		writeJSONResponse(w, ollamaEndTurnResponse("done"))
	})

	_, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID:       "task1",
		Prompt:       "Do something.",
		SystemPrompt: "You are a test assistant.",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(capturedMessages) < 2 {
		t.Fatalf("expected at least 2 messages (system + user), got %d", len(capturedMessages))
	}
	if capturedMessages[0]["role"] != "system" {
		t.Errorf("expected first message role 'system', got %q", capturedMessages[0]["role"])
	}
	if capturedMessages[1]["role"] != "user" {
		t.Errorf("expected second message role 'user', got %q", capturedMessages[1]["role"])
	}
}

func TestOllamaExecutor_Execute_WriteFileTool(t *testing.T) {
	callCount := 0
	ex, artifactStorage := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount == 1 {
			writeJSONResponse(w, ollamaToolUseResponse("call_1", "write_file", map[string]string{
				"path":    "result.md",
				"content": "# Result",
			}))
			return
		}
		writeJSONResponse(w, ollamaEndTurnResponse("File written."))
	})

	result, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "writetask",
		Prompt: "Write a file.",
		Tools:  []string{"write_file"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Output != "File written." {
		t.Errorf("expected output %q, got %q", "File written.", result.Output)
	}
	if len(result.Files) != 1 || result.Files[0] != "writetask/result.md" {
		t.Errorf("expected Files [writetask/result.md], got %v", result.Files)
	}
	content, err := artifactStorage.Read(context.Background(), "writetask/result.md")
	if err != nil {
		t.Fatalf("storage.Read: %v", err)
	}
	if string(content) != "# Result" {
		t.Errorf("expected file content %q, got %q", "# Result", string(content))
	}
}

func TestOllamaExecutor_Execute_ReadFileTool(t *testing.T) {
	callCount := 0
	var capturedToolResult string

	ex, artifactStorage := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount == 1 {
			writeJSONResponse(w, ollamaToolUseResponse("call_1", "read_file", map[string]string{
				"path": "source.txt",
			}))
			return
		}
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		capturedToolResult = extractOllamaToolResult(body)
		writeJSONResponse(w, ollamaEndTurnResponse("Read done."))
	})

	_ = artifactStorage.Write(context.Background(), "readtask/source.txt", []byte("hello content"))

	_, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "readtask",
		Prompt: "Read a file.",
		Tools:  []string{"read_file"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if capturedToolResult != "hello content" {
		t.Errorf("expected tool result %q, got %q", "hello content", capturedToolResult)
	}
}

func TestOllamaExecutor_Execute_ListFilesTool(t *testing.T) {
	callCount := 0
	var capturedToolResult string

	ex, artifactStorage := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount == 1 {
			writeJSONResponse(w, ollamaToolUseResponse("call_1", "list_files", map[string]string{
				"pattern": "*.md",
			}))
			return
		}
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		capturedToolResult = extractOllamaToolResult(body)
		writeJSONResponse(w, ollamaEndTurnResponse("Listed."))
	})

	_ = artifactStorage.Write(context.Background(), "listtask/a.md", []byte("a"))
	_ = artifactStorage.Write(context.Background(), "listtask/b.md", []byte("b"))

	_, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "listtask",
		Prompt: "List files.",
		Tools:  []string{"list_files"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(capturedToolResult, "listtask/a.md") || !strings.Contains(capturedToolResult, "listtask/b.md") {
		t.Errorf("expected both files in tool result, got %q", capturedToolResult)
	}
}

func TestOllamaExecutor_Execute_APIError(t *testing.T) {
	ex, _ := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":{"message":"model not found"}}`, http.StatusNotFound)
	})

	_, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "errtask",
		Prompt: "Do something.",
	})
	if err == nil {
		t.Fatal("expected error from API failure, got nil")
	}
}

func TestOllamaExecutor_Execute_MaxStepsExceeded(t *testing.T) {
	ex, _ := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(w, ollamaToolUseResponse("call_1", "write_file", map[string]string{
			"path":    "out.txt",
			"content": "x",
		}))
	})

	_, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "steptask",
		Prompt: "Loop forever.",
		Tools:  []string{"write_file"},
	})
	if err == nil {
		t.Fatal("expected error when max steps exceeded, got nil")
	}
	if !strings.Contains(err.Error(), "did not complete") {
		t.Errorf("expected 'did not complete' in error, got %q", err.Error())
	}
}

func TestOllamaExecutor_Execute_DefaultModelUsedWhenEmpty(t *testing.T) {
	var capturedModel string
	ex, _ := newOllamaTestExecutor(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if m, ok := body["model"].(string); ok {
			capturedModel = m
		}
		writeJSONResponse(w, ollamaEndTurnResponse("done"))
	})

	_, err := ex.Execute(context.Background(), ports.ExecutionRequest{
		TaskID: "t",
		Prompt: "p",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if capturedModel != ollamaDefaultModel {
		t.Errorf("expected default model %q, got %q", ollamaDefaultModel, capturedModel)
	}
}
