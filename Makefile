SHELL := /bin/bash

PYTHON ?= python3
API_MODE ?= mock
MOCK_SCENARIO ?= normal
P0_DATA_PROFILE ?= config/data-profile.json
P0_PIPELINE_ROOT ?= .

.PHONY: dev preview risk api-types codex-preflight codex-postflight codex-version-policy check-fast check-integration check-data-p0 check-release check-release-full release-prepare release-activate release-rollback release-gc

dev:
	DOMEYE_MOCK_SCENARIO="$(MOCK_SCENARIO)" $(PYTHON) dev/run_local.py dev --api "$(API_MODE)"

preview:
	DOMEYE_MOCK_SCENARIO="$(MOCK_SCENARIO)" $(PYTHON) dev/run_local.py preview --api mock

risk:
	$(PYTHON) dev/checks.py risk $(if $(BASE_REF),--base-ref "$(BASE_REF)")

api-types:
	npm --prefix frontend run api:types

codex-preflight:
	$(PYTHON) dev/codex_task_guard.py preflight

codex-postflight:
	$(PYTHON) dev/codex_task_guard.py postflight --run-checks

codex-version-policy:
	@test -n "$(BASE_REF)" || { echo '缺少 BASE_REF'; exit 2; }
	$(PYTHON) dev/codex_task_guard.py policy --base-ref "$(BASE_REF)"

check-fast:
	$(PYTHON) dev/checks.py fast $(if $(BASE_REF),--base-ref "$(BASE_REF)")

check-integration:
	$(PYTHON) dev/checks.py integration $(if $(BASE_REF),--base-ref "$(BASE_REF)")

check-data-p0:
	@if test "$(P0_QUALITY_FIXTURE)" = "1"; then \
		$(PYTHON) -m unittest dev.tests.test_p0_quality_gate dev.tests.test_p0_quality_gate_cli dev.tests.test_p0_reproducibility; \
	else \
		test -n "$(P0_D2_MANIFEST)" || { echo '缺少 P0_D2_MANIFEST'; exit 2; }; \
		test -n "$(P0_D2_CHECKSUMS)" || { echo '缺少 P0_D2_CHECKSUMS'; exit 2; }; \
		test -n "$(P0_D3_MANIFEST)" || { echo '缺少 P0_D3_MANIFEST'; exit 2; }; \
		test -n "$(P0_D3_VERIFICATION_SUMMARY)" || { echo '缺少 P0_D3_VERIFICATION_SUMMARY'; exit 2; }; \
		test -n "$(P0_D3_CHECKSUMS)" || { echo '缺少 P0_D3_CHECKSUMS'; exit 2; }; \
		test -n "$(P0_EXECUTION_CONTEXT)" || { echo '缺少 P0_EXECUTION_CONTEXT'; exit 2; }; \
		test -n "$(P0_EXECUTION_CHECKSUMS)" || { echo '缺少 P0_EXECUTION_CHECKSUMS'; exit 2; }; \
		test -n "$(P0_QUALITY_OUTPUT_DIR)" || { echo '缺少 P0_QUALITY_OUTPUT_DIR'; exit 2; }; \
		test -z "$(P0_METRIC_SUMMARY)" || test -n "$(P0_METRIC_MANIFEST)" || { echo '提供 P0_METRIC_SUMMARY 时缺少 P0_METRIC_MANIFEST'; exit 2; }; \
		$(PYTHON) dev/data_quality/p0_quality_gate.py \
			--data-profile "$(P0_DATA_PROFILE)" \
			--d2-manifest "$(P0_D2_MANIFEST)" \
			--d2-checksums "$(P0_D2_CHECKSUMS)" \
			--d3-manifest "$(P0_D3_MANIFEST)" \
			--d3-verification-summary "$(P0_D3_VERIFICATION_SUMMARY)" \
			--d3-checksums "$(P0_D3_CHECKSUMS)" \
			--execution-context "$(P0_EXECUTION_CONTEXT)" \
			--execution-checksums "$(P0_EXECUTION_CHECKSUMS)" \
			--pipeline-root "$(P0_PIPELINE_ROOT)" \
			--output-dir "$(P0_QUALITY_OUTPUT_DIR)" \
			$(if $(P0_ROUTE_SUMMARY),--route-summary "$(P0_ROUTE_SUMMARY)" --route-checksums "$(P0_ROUTE_CHECKSUMS)") \
			$(if $(P0_METRIC_SUMMARY),--metric-summary "$(P0_METRIC_SUMMARY)" --metric-manifest "$(P0_METRIC_MANIFEST)" --metric-checksums "$(P0_METRIC_CHECKSUMS)") \
			$(if $(P0_REPRODUCIBILITY_SUMMARY),--reproducibility-summary "$(P0_REPRODUCIBILITY_SUMMARY)" --reproducibility-checksums "$(P0_REPRODUCIBILITY_CHECKSUMS)"); \
	fi

check-release:
	$(PYTHON) dev/checks.py release

check-release-full:
	@echo '拒绝执行：check-* 命令不允许恢复数据库或切换生产服务。'
	@echo '请使用独立的 release-prepare / release-activate 流程。'
	@exit 2

release-prepare:
	@test -n "$(RELEASE_DIR)" || { echo '缺少 RELEASE_DIR'; exit 2; }
	@test -n "$(HIDDEN_PATH)" || { echo '缺少 HIDDEN_PATH'; exit 2; }
	@test -n "$(DATABASE_ENV_FILE)" || { echo '缺少 DATABASE_ENV_FILE'; exit 2; }
	@test -n "$(RELEASE_HOST)" || { echo '缺少 RELEASE_HOST'; exit 2; }
	./deploy/release/prepare.sh "$(RELEASE_DIR)" "$(HIDDEN_PATH)" "$(DATABASE_ENV_FILE)" "$(RELEASE_HOST)"

release-activate:
	@test -n "$(RELEASE_DIR)" || { echo '缺少 RELEASE_DIR'; exit 2; }
	@test -n "$(HIDDEN_PATH)" || { echo '缺少 HIDDEN_PATH'; exit 2; }
	@test -n "$(DATABASE_ENV_FILE)" || { echo '缺少 DATABASE_ENV_FILE'; exit 2; }
	@test -n "$(RELEASE_HOST)" || { echo '缺少 RELEASE_HOST'; exit 2; }
	@test -n "$(CONFIRM_RELEASE_ID)" || { echo '缺少 CONFIRM_RELEASE_ID'; exit 2; }
	CONFIRM_RELEASE_ID="$(CONFIRM_RELEASE_ID)" ./deploy/release/activate.sh "$(RELEASE_DIR)" "$(HIDDEN_PATH)" "$(DATABASE_ENV_FILE)" "$(RELEASE_HOST)"

release-rollback:
	@test -n "$(RELEASE_ID)" || { echo '缺少 RELEASE_ID'; exit 2; }
	@test -n "$(DATABASE_ENV_FILE)" || { echo '缺少 DATABASE_ENV_FILE'; exit 2; }
	@test -n "$(RELEASE_HOST)" || { echo '缺少 RELEASE_HOST'; exit 2; }
	@test "$(CONFIRM_RELEASE_ID)" = "$(RELEASE_ID)" || { echo 'CONFIRM_RELEASE_ID 必须与 RELEASE_ID 完全一致'; exit 2; }
	CONFIRM_RELEASE_ID="$(CONFIRM_RELEASE_ID)" ./deploy/release/rollback.sh "$(RELEASE_ID)" "$(DATABASE_ENV_FILE)" "$(RELEASE_HOST)"

release-gc:
	@if test "$(GC_EXECUTE)" = "1"; then \
		test -n "$(GC_RELEASE_ID)" || { echo '执行 GC 缺少 GC_RELEASE_ID'; exit 2; }; \
		test -n "$(RELEASE_HOST)" || { echo '执行 GC 缺少 RELEASE_HOST'; exit 2; }; \
		test "$(CONFIRM_RELEASE_ID)" = "$(GC_RELEASE_ID)" || { echo 'CONFIRM_RELEASE_ID 必须与 GC_RELEASE_ID 完全一致'; exit 2; }; \
		CONFIRM_RELEASE_ID="$(CONFIRM_RELEASE_ID)" ./deploy/release/gc.sh --execute --release-id "$(GC_RELEASE_ID)" --older-than-days "$(or $(GC_OLDER_THAN_DAYS),14)" --host "$(RELEASE_HOST)"; \
	else \
		./deploy/release/gc.sh $(if $(GC_RELEASE_ID),--release-id "$(GC_RELEASE_ID)") --older-than-days "$(or $(GC_OLDER_THAN_DAYS),14)"; \
	fi
