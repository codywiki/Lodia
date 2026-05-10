package config

import "testing"

func TestProductionProfileRecognizesChinaIndependent(t *testing.T) {
	if !(Config{Deployment: "china_independent"}).ProductionProfile() {
		t.Fatalf("china_independent should use production safeguards")
	}
	if !(Config{Deployment: "production"}).ProductionProfile() {
		t.Fatalf("production should use production safeguards")
	}
	if (Config{Deployment: "internal_test"}).ProductionProfile() {
		t.Fatalf("internal_test should not use production safeguards")
	}
}
