.PHONY: console test

# Rebuild the web console (Node required); output is committed into
# src/syros/console/static/ so pip installs and Docker need no Node.
console:
	cd console && npm install && npm run build

test:
	uv run pytest tests/ -q
