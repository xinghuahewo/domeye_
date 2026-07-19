SHELL := /bin/bash

PYTHON ?= python3
API_MODE ?= mock
MOCK_SCENARIO ?= normal

.PHONY: dev preview risk api-types check-fast check-integration check-release check-release-full

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

check-release-full: check-release
	@test "$(CONFIRM_FULL_RELEASE)" = "1" || { echo '拒绝执行：必须显式设置 CONFIRM_FULL_RELEASE=1'; exit 2; }
	@test -n "$(RELEASE_DIR)" || { echo '缺少 RELEASE_DIR'; exit 2; }
	@test -n "$(HIDDEN_PATH)" || { echo '缺少 HIDDEN_PATH'; exit 2; }
	./deploy/acceptance/full-acceptance.sh "$(RELEASE_DIR)" "$(HIDDEN_PATH)" $(if $(DATABASE_ENV_FILE),"$(DATABASE_ENV_FILE)")
