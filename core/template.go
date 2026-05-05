package core

import (
	"fmt"
	"regexp"
	"strings"
)

var templatePattern = regexp.MustCompile(`\{\{\s*([^}]+?)\s*\}\}`)

// Resolve replaces all {{ }} template expressions in the input string
// using values from the RunContext.
//
// Supported expressions:
//   - {{ input.key }}         → runtime input value
//   - {{ tasks.id }}          → primary output of a completed task
//   - {{ tasks.id.files }}    → newline-separated list of files produced by a task
//   - {{ tasks.id.slug }}     → path of a specific output file matched by slug
func Resolve(template string, context *RunContext) (string, error) {
	var resolveErr error
	result := templatePattern.ReplaceAllStringFunc(template, func(match string) string {
		if resolveErr != nil {
			return match
		}
		inner := strings.TrimSpace(match[2 : len(match)-2])
		value, err := resolveExpression(inner, context)
		if err != nil {
			resolveErr = err
			return match
		}
		return value
	})
	if resolveErr != nil {
		return "", resolveErr
	}
	return result, nil
}

func resolveExpression(expression string, context *RunContext) (string, error) {
	parts := strings.SplitN(expression, ".", 3)
	switch parts[0] {
	case "input":
		if len(parts) < 2 {
			return "", fmt.Errorf("template: invalid input reference %q", expression)
		}
		value, ok := context.GetInput(parts[1])
		if !ok {
			return "", fmt.Errorf("template: input %q not found", parts[1])
		}
		return value, nil

	case "tasks":
		if len(parts) < 2 {
			return "", fmt.Errorf("template: invalid task reference %q", expression)
		}
		taskID := parts[1]
		result, ok := context.GetTaskResult(taskID)
		if !ok {
			return "", fmt.Errorf("template: task %q result not available", taskID)
		}
		if len(parts) == 2 {
			return result.Output, nil
		}
		switch parts[2] {
		case "files":
			return strings.Join(result.Files, "\n"), nil
		default:
			slug := parts[2]
			for _, file := range result.Files {
				if fileSlug(file) == slug {
					return file, nil
				}
			}
			return "", fmt.Errorf("template: task %q has no file matching slug %q", taskID, slug)
		}

	default:
		return "", fmt.Errorf("template: unknown reference type %q in %q", parts[0], expression)
	}
}

// fileSlug converts a file path's basename to a slug for template references.
// Example: "tokens.css" → "tokens_css", "design-system.md" → "design_system_md"
func fileSlug(filename string) string {
	parts := strings.Split(filename, "/")
	base := parts[len(parts)-1]
	base = strings.ReplaceAll(base, ".", "_")
	base = strings.ReplaceAll(base, "-", "_")
	return base
}
