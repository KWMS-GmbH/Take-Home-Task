.PHONY: verify-assignment test-assignment-guard

verify-assignment:
	@./scripts/verify-assignment.sh

test-assignment-guard:
	@PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_assignment_guard.py' -v
