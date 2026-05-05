package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/janmarkuslanger/stagehand/ports"
)

const maxAgentSteps = 20
const defaultModel = "claude-opus-4-7"

// ClaudeExecutor runs a task by calling the Anthropic Messages API and
// dispatching tool calls to the provided ArtifactStorage.
type ClaudeExecutor struct {
	client  *anthropic.Client
	storage ports.ArtifactStorage
}

// NewClaudeExecutor creates a ClaudeExecutor backed by the given API client and storage.
func NewClaudeExecutor(client *anthropic.Client, storage ports.ArtifactStorage) *ClaudeExecutor {
	return &ClaudeExecutor{client: client, storage: storage}
}

// Execute runs the task described by request, returning the agent's final text
// output and the paths of any files written during execution.
func (e *ClaudeExecutor) Execute(ctx context.Context, request ports.ExecutionRequest) (ports.ExecutionResult, error) {
	model := request.Model
	if model == "" {
		model = defaultModel
	}

	systemPrompt := request.SystemPrompt
	if systemPrompt == "" {
		systemPrompt = "You are a helpful AI assistant."
	}

	tools := buildTools(request.Tools)

	messages := []anthropic.MessageParam{
		anthropic.NewUserMessage(anthropic.NewTextBlock(request.Prompt)),
	}

	var finalOutput string
	var writtenFiles []string
	var lastStopReason anthropic.StopReason

	for step := 0; step < maxAgentSteps; step++ {
		resp, err := e.client.Messages.New(ctx, anthropic.MessageNewParams{
			Model:     anthropic.Model(model),
			MaxTokens: 16000,
			System: []anthropic.TextBlockParam{
				{
					Text:         systemPrompt,
					CacheControl: anthropic.NewCacheControlEphemeralParam(),
				},
			},
			Messages: messages,
			Tools:    tools,
		})
		if err != nil {
			return ports.ExecutionResult{}, fmt.Errorf("claude executor: task %s: step %d: %w", request.TaskID, step, err)
		}

		lastStopReason = resp.StopReason

		for _, block := range resp.Content {
			if textBlock, ok := block.AsAny().(anthropic.TextBlock); ok {
				finalOutput = textBlock.Text
			}
		}

		if resp.StopReason != anthropic.StopReasonToolUse {
			break
		}

		messages = append(messages, resp.ToParam())

		var toolResults []anthropic.ContentBlockParamUnion
		for _, block := range resp.Content {
			toolUse, ok := block.AsAny().(anthropic.ToolUseBlock)
			if !ok {
				continue
			}
			resultContent, toolErr := e.dispatchTool(ctx, request.TaskID, toolUse, &writtenFiles)
			if toolErr != nil {
				toolResults = append(toolResults, anthropic.NewToolResultBlock(toolUse.ID, toolErr.Error(), true))
			} else {
				toolResults = append(toolResults, anthropic.NewToolResultBlock(toolUse.ID, resultContent, false))
			}
		}

		messages = append(messages, anthropic.NewUserMessage(toolResults...))
	}

	if lastStopReason == anthropic.StopReasonToolUse {
		return ports.ExecutionResult{}, fmt.Errorf("claude executor: task %s: agent did not complete within %d steps", request.TaskID, maxAgentSteps)
	}

	return ports.ExecutionResult{Output: finalOutput, Files: writtenFiles}, nil
}

func (e *ClaudeExecutor) dispatchTool(ctx context.Context, taskID string, toolUse anthropic.ToolUseBlock, writtenFiles *[]string) (string, error) {
	switch toolUse.Name {
	case "write_file":
		return e.executeWriteFile(ctx, taskID, toolUse.Input, writtenFiles)
	case "read_file":
		return e.executeReadFile(ctx, taskID, toolUse.Input)
	case "list_files":
		return e.executeListFiles(ctx, taskID, toolUse.Input)
	default:
		return "", fmt.Errorf("unknown tool %q", toolUse.Name)
	}
}

type writeFileInput struct {
	Path    string `json:"path"`
	Content string `json:"content"`
}

func (e *ClaudeExecutor) executeWriteFile(ctx context.Context, taskID string, rawInput json.RawMessage, writtenFiles *[]string) (string, error) {
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

type readFileInput struct {
	Path string `json:"path"`
}

func (e *ClaudeExecutor) executeReadFile(ctx context.Context, taskID string, rawInput json.RawMessage) (string, error) {
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

type listFilesInput struct {
	Pattern string `json:"pattern"`
}

func (e *ClaudeExecutor) executeListFiles(ctx context.Context, taskID string, rawInput json.RawMessage) (string, error) {
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

func buildTools(toolNames []string) []anthropic.ToolUnionParam {
	available := map[string]anthropic.ToolParam{
		"write_file": {
			Name:        "write_file",
			Description: anthropic.String("Write text content to a file. Creates the file if it does not exist."),
			InputSchema: anthropic.ToolInputSchemaParam{
				Properties: map[string]any{
					"path":    map[string]any{"type": "string", "description": "Relative file path"},
					"content": map[string]any{"type": "string", "description": "Text content to write"},
				},
				Required: []string{"path", "content"},
			},
		},
		"read_file": {
			Name:        "read_file",
			Description: anthropic.String("Read the text content of a file."),
			InputSchema: anthropic.ToolInputSchemaParam{
				Properties: map[string]any{
					"path": map[string]any{"type": "string", "description": "Relative file path"},
				},
				Required: []string{"path"},
			},
		},
		"list_files": {
			Name:        "list_files",
			Description: anthropic.String("List files matching a glob pattern."),
			InputSchema: anthropic.ToolInputSchemaParam{
				Properties: map[string]any{
					"pattern": map[string]any{"type": "string", "description": "Glob pattern, e.g. *.md"},
				},
				Required: []string{"pattern"},
			},
		},
	}

	var result []anthropic.ToolUnionParam
	for _, name := range toolNames {
		if tool, ok := available[name]; ok {
			toolCopy := tool
			result = append(result, anthropic.ToolUnionParam{OfTool: &toolCopy})
		}
	}
	return result
}

// Compile-time check that ClaudeExecutor satisfies ports.AgentExecutor.
var _ ports.AgentExecutor = (*ClaudeExecutor)(nil)
