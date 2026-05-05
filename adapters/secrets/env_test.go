package secrets

import (
	"context"
	"testing"
)

func TestEnvSecretProvider_Get(t *testing.T) {
	t.Setenv("STAGEHAND_TEST_SECRET", "super-secret")

	provider := NewEnvSecretProvider()
	value, err := provider.Get(context.Background(), "STAGEHAND_TEST_SECRET")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if value != "super-secret" {
		t.Errorf("expected %q, got %q", "super-secret", value)
	}
}

func TestEnvSecretProvider_Get_Unset(t *testing.T) {
	provider := NewEnvSecretProvider()
	_, err := provider.Get(context.Background(), "STAGEHAND_DEFINITELY_UNSET_VAR_XYZ")
	if err == nil {
		t.Fatal("expected error for unset variable, got nil")
	}
}

func TestEnvSecretProvider_Get_EmptyValue(t *testing.T) {
	t.Setenv("STAGEHAND_EMPTY_SECRET", "")

	provider := NewEnvSecretProvider()
	_, err := provider.Get(context.Background(), "STAGEHAND_EMPTY_SECRET")
	if err == nil {
		t.Fatal("expected error for empty variable, got nil")
	}
}
