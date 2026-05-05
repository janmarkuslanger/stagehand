package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/openai/openai-go"
	"github.com/openai/openai-go/option"
	"github.com/openai/openai-go/shared"
	"github.com/janmarkuslanger/stagehand/ports"
)

const ollamaDefaultModel = "qwen2.5"
const ollamaDefaultHost = "http://localhost:11434"

// OllamaExecutor runs a task by calling a local Ollama instance via its
// OpenAI-compatible chat completions endpoint.
type OllamaExecutor struct {
	client  *openai.Client
	storage ports.ArtifactStorage
}

// NewOllamaExecutor creates an OllamaExecutor that calls the given host and
// stores artifacts in the provided storage.
func NewOllamaExecutor(host string, storage ports.ArtifactStorage) *OllamaExecutor {
	client := openai.NewClient(
		option.WithBaseURL(host+"/v1"),
		option.WithAPIKey("ollama"),
	)
	return &OllamaExecutor{client: &client, storage: storage}
}

// Execute runs the task described by request against Ollama and returns the
// agent's final text output and any files written during execution.
func (e *OllamaExecutor) Execute(ctx context.Context, request ports.ExecutionRequest) (ports.ExecutionResult, error) {
	model := request.Model
	if model == "" {
		model = ollamaDefaultModel
	}

	tools := buildOllamaTools(request.Tools)

	messages := []openai.ChatCompletionMessageParamUnion{
		openai.UserMessage(request.Prompt),
	}

	if request.SystemPrompt != "" {
		messages = append([]openai.ChatCompletionMessageParamUnion{
			openai.SystemMessage(request.SystemPrompt),
		}, messages...)
	}

	var finalOutput string
	var writtenFiles []string
	var lastFinishReason string

	for step := 0; step < maxAgentSteps; step++ {
		params := openai.ChatCompletionNewParams{
			Model:    model,
			Messages: messages,
		}
		if len(tools) > 0 {
			params.Tools = tools
		}

		resp, err := e.client.Chat.Completions.New(ctx, params)
		if err != nil {
			return ports.ExecutionResult{}, fmt.Errorf("ollama executor: task %s: step %d: %w", request.TaskID, step, err)
		}
		if len(resp.Choices) == 0 {
			return ports.ExecutionResult{}, fmt.Errorf("ollama executor: task %s: step %d: empty response", request.TaskID, step)
		}

		choice := resp.Choices[0]
		lastFinishReason = string(choice.FinishReason)

		if choice.Message.Content != "" {
			finalOutput = choice.Message.Content
		}

		if choice.FinishReason != "tool_calls" {
			break
		}

		messages = append(messages, choice.Message.ToParam())

		var toolResults []openai.ChatCompletionMessageParamUnion
		for _, toolCall := range choice.Message.ToolCalls {
			resultContent, toolErr := e.dispatchOllamaTool(ctx, request.TaskID, toolCall, &writtenFiles)
			if toolErr != nil {
				toolResults = append(toolResults, openai.ToolMessage(toolErr.Error(), toolCall.ID))
			} else {
				toolResults = append(toolResults, openai.ToolMessage(resultContent, toolCall.ID))
			}
		}
		messages = append(messages, toolResults...)
	}

	if lastFinishReason == "tool_calls" {
		return ports.ExecutionResult{}, fmt.Errorf("ollama executor: task %s: agent did not complete within %d steps", request.TaskID, maxAgentSteps)
	}

	return ports.ExecutionResult{Output: finalOutput, Files: writtenFiles}, nil
}

func (e *OllamaExecutor) dispatchOllamaTool(ctx context.Context, taskID string, toolCall openai.ChatCompletionMessageToolCall, writtenFiles *[]string) (string, error) {
	rawInput := json.RawMessage(toolCall.Function.Arguments)
	switch toolCall.Function.Name {
	case "write_file":
		return e.executeOllamaWriteFile(ctx, taskID, rawInput, writtenFiles)
	case "read_file":
		return e.executeOllamaReadFile(ctx, taskID, rawInput)
	case "list_files":
		return e.executeOllamaListFiles(ctx, taskID, rawInput)
	default:
		return "", fmt.Errorf("unknown tool %q", toolCall.Function.Name)
	}
}

func (e *OllamaExecutor) executeOllamaWriteFile(ctx context.Context, taskID string, rawInput json.RawMessage, writtenFiles *[]string) (string, error) {
	var input writeFileInput
	if err := json.Unmarshal(rawInput, &input); err != nil {
		return "", fmt.Errorf("write_file: invalid input: %w", err)
	}
	if input.Path == "" {
		return "", fmt.Errorf("write_file: path is required")
	}
	storagePath := taskID + "/" + input.Path
	if err := e.storage.Write(ctx, storagePath, []byte(input.Content)); err != nil {
		return "", fmt.Errorf("write_file: %w", err)
	}
	*writtenFiles = append(*writtenFiles, storagePath)
	return "ok", nil
}

func (e *OllamaExecutor) executeOllamaReadFile(ctx context.Context, taskID string, rawInput json.RawMessage) (string, error) {
	var input readFileInput
	if err := json.Unmarshal(rawInput, &input); err != nil {
		return "", fmt.Errorf("read_file: invalid input: %w", err)
	}
	if input.Path == "" {
		return "", fmt.Errorf("read_file: path is required")
	}
	data, err := e.storage.Read(ctx, taskID+"/"+input.Path)
	if err != nil {
		return "", fmt.Errorf("read_file: %w", err)
	}
	return string(data), nil
}

func (e *OllamaExecutor) executeOllamaListFiles(ctx context.Context, taskID string, rawInput json.RawMessage) (string, error) {
	var input listFilesInput
	if err := json.Unmarshal(rawInput, &input); err != nil {
		return "", fmt.Errorf("list_files: invalid input: %w", err)
	}
	pattern := taskID + "/*"
	if input.Pattern != "" {
		pattern = taskID + "/" + input.Pattern
	}
	files, err := e.storage.List(ctx, pattern)
	if err != nil {
		return "", fmt.Errorf("list_files: %w", err)
	}
	return strings.Join(files, "\n"), nil
}

func buildOllamaTools(toolNames []string) []openai.ChatCompletionToolParam {
	available := map[string]openai.ChatCompletionToolParam{
		"write_file": {
			Function: shared.FunctionDefinitionParam{
				Name:        "write_file",
				Description: openai.String("Write text content to a file. Creates the file if it does not exist."),
				Parameters: shared.FunctionParameters{
					"type": "object",
					"properties": map[string]any{
						"path":    map[string]any{"type": "string", "description": "Relative file path"},
						"content": map[string]any{"type": "string", "description": "Text content to write"},
					},
					"required": []string{"path", "content"},
				},
			},
		},
		"read_file": {
			Function: shared.FunctionDefinitionParam{
				Name:        "read_file",
				Description: openai.String("Read the text content of a file."),
				Parameters: shared.FunctionParameters{
					"type": "object",
					"properties": map[string]any{
						"path": map[string]any{"type": "string", "description": "Relative file path"},
					},
					"required": []string{"path"},
				},
			},
		},
		"list_files": {
			Function: shared.FunctionDefinitionParam{
				Name:        "list_files",
				Description: openai.String("List files matching a glob pattern."),
				Parameters: shared.FunctionParameters{
					"type": "object",
					"properties": map[string]any{
						"pattern": map[string]any{"type": "string", "description": "Glob pattern, e.g. *.md"},
					},
					"required": []string{"pattern"},
				},
			},
		},
	}

	var result []openai.ChatCompletionToolParam
	for _, name := range toolNames {
		if tool, ok := available[name]; ok {
			result = append(result, tool)
		}
	}
	return result
}

// Compile-time check that OllamaExecutor satisfies ports.AgentExecutor.
var _ ports.AgentExecutor = (*OllamaExecutor)(nil)
