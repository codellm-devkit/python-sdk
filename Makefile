# Makefile targets for dvelopment an testing
# Use make help for more info

.PHONY: help
help: ## Display this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

.PHONY: all
all: help

##@ Development

.PHONY: venv
venv: ## Create a Python virtual environment
	$(info Creating Python 3 virtual environment...)
	uv venv

.PHONY: install
install: ## Install Python dependencies in virtual environment
	$(info Installing dependencies...)
	uv sync --all-groups

.PHONY: lint
lint: ## Run the linter
	$(info Running linting...)
	uv run flake8 cldk --count --select=E9,F63,F7,F82 --show-source --statistics
	uv run flake8 cldk --count --max-complexity=10 --max-line-length=180 --statistics
	uv run pylint cldk --max-line-length=180

.PHONY: test
test: ## Run the unit tests
	$(info Running tests...)
	uv run pytest --pspec --cov=cldk --cov-fail-under=75 --disable-warnings

##@ Build

.PHONY: clean
clean: ## Cleans up from previous compiles
	$(info Cleaning up compile artifacts...)
	rm -fr dist

.PHONY: refresh
refresh: ## Refresh code analyzer
	$(info Refreshing CodeAnalyzer...)
	wget $(curl -s https://api.github.com/repos/IBM/codenet-minerva-code-analyzer/releases/latest | grep "browser_download_url" | grep codeanalyzer.jar | cut -d '"' -f 4)
	mv codeanalyzer.jar cldk/analysis/java/codeanalyzer/jar/codeanalyzer.jar

.PHONY: build
build: ## Builds a new Python wheel
	$(info Building artifacts...)

	# Inject the latest Code Analyzer JAR
	wget -q $(shell curl -s https://api.github.com/repos/IBM/codenet-minerva-code-analyzer/releases/latest | jq -r '.assets[] | .browser_download_url')
	mkdir -p cldk/analysis/java/codeanalyzer/jar/
	mv codeanalyzer-*.jar cldk/analysis/java/codeanalyzer/jar/

	# Build the package
	uv build
