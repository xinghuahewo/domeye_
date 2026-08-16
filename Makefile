SHELL := /bin/bash

PYTHON ?= python3
API_MODE ?= mock
MOCK_SCENARIO ?= normal

.PHONY: dev preview risk api-types check-fast check-integration check-release check-release-full release-prepare release-activate release-rollback release-gc

dev:
	DOMEYE_MOCK_SCENARIO="$(MOCK_SCENARIO)" $(PYTHON) dev/run_local.py dev --api "$(API_MODE)"

preview:
	DOMEYE_MOCK_SCENARIO="$(MOCK_SCENARIO)" $(PYTHON) dev/run_local.py preview --api mock

risk:
	$(PYTHON) dev/checks.py risk $(if $(BASE_REF),--base-ref "$(BASE_REF)")

api-types:
	npm --prefix frontend run api:types

check-fast:
	$(PYTHON) dev/checks.py fast $(if $(BASE_REF),--base-ref "$(BASE_REF)")

check-integration:
	$(PYTHON) dev/checks.py integration $(if $(BASE_REF),--base-ref "$(BASE_REF)")

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
