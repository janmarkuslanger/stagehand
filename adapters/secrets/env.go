package secrets

import (
	"context"
	"fmt"
	"os"

	"github.com/janmarkuslanger/stagehand/ports"
)

// EnvSecretProvider resolves secrets from environment variables.
type EnvSecretProvider struct{}

// NewEnvSecretProvider creates an EnvSecretProvider.
func NewEnvSecretProvider() *EnvSecretProvider {
	return &EnvSecretProvider{}
}

// Get returns the value of the environment variable named key.
// Returns an error if the variable is not set or empty.
func (p *EnvSecretProvider) Get(_ context.Context, key string) (string, error) {
	value := os.Getenv(key)
	if value == "" {
		return "", fmt.Errorf("env secrets: %q is not set", key)
	}
	return value, nil
}

// Compile-time check that EnvSecretProvider satisfies ports.SecretProvider.
var _ ports.SecretProvider = (*EnvSecretProvider)(nil)
