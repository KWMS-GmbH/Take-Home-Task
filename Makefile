.PHONY: verify-assignment test-assignment-guard check test build deploy demo-data logs teardown smoke

verify-assignment:
	@./scripts/verify-assignment.sh

test-assignment-guard:
	@PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_assignment_guard.py' -v

check:
	@./scripts/check.sh

test:
	@./scripts/test.sh

build:
	@docker build --target backend --tag kwms-backend:local .
	@docker build --target frontend --tag kwms-frontend:local .

deploy:
	@./deploy.sh

demo-data:
	@./scripts/demo-data.sh

logs:
	@./scripts/logs.sh

teardown:
	@./scripts/teardown.sh

smoke:
	@./scripts/smoke.sh
