.PHONY: test bench check format

test:
	uv run coverage run -m pytest tests --ignore=tests/test_bench.py

bench:
	uv run pytest tests/test_bench.py -v -s -p no:logging --no-header 2>/dev/null

format:
	deno fmt --config parselbox/sandbox/deno.jsonc parselbox/sandbox
	uv run ruff format .
	uv run ruff check --fix .
	uvx pre-commit run --all-files

check:
	deno lint --config parselbox/sandbox/deno.jsonc parselbox/sandbox
	uv run ruff check .
	uv run ruff format --check .
	make test

push:
	git add .
	git commit -m "$(filter-out $@,$(MAKECMDGOALS))"
	git push

%:
	@:
