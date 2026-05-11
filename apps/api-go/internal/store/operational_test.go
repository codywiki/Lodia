package store

import (
	"context"
	"testing"
)

func TestActiveDisputeBlockersSkipsEmptyEntitySets(t *testing.T) {
	db := &DB{}
	blockers, err := db.ActiveDisputeBlockers(context.Background(), map[string][]string{
		"case":        {"", "   "},
		"contributor": nil,
	}, true, 0)
	if err != nil {
		t.Fatalf("unexpected error for empty entity sets: %v", err)
	}
	if len(blockers) != 0 {
		t.Fatalf("expected no blockers, got %#v", blockers)
	}
}

func TestCleanUniqueStringsTrimSortsAndDedupes(t *testing.T) {
	got := cleanUniqueStrings([]string{" case_b ", "", "case_a", "case_b", "  case_a"})
	want := []string{"case_a", "case_b"}
	if len(got) != len(want) {
		t.Fatalf("unexpected length: got %#v want %#v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("unexpected cleaned values: got %#v want %#v", got, want)
		}
	}
}
