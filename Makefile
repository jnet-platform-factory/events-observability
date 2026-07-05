# ---------------------------------------------------------------------------
# Serverless Events Observability — build / package / publish to the AWS
# Serverless Application Repository (SAR), plus local test deploys.
#
# Quick start:
#   make release S3_BUCKET=my-sar-artifacts        # validate + build + package + publish
#   make deploy                                    # guided deploy into your own account (testing)
#
# Auth: either export AWS_PROFILE, or set AWS_VAULT=<profile> to wrap every
# command in `aws-vault exec` (matches this repo's workflow), e.g.
#   make build AWS_VAULT=dev
# ---------------------------------------------------------------------------

APP_NAME    := serverless-events-observability
REGION      ?= us-east-1
STACK_NAME  ?= events-observability
S3_BUCKET   ?=
TEMPLATE    := template.yaml
PACKAGED    := packaged.yaml

# Optional aws-vault wrapper: `make ... AWS_VAULT=dev`
AWS_VAULT ?=
ifeq ($(AWS_VAULT),)
RUN :=
else
RUN := aws-vault exec $(AWS_VAULT) --
endif

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Vars: REGION=$(REGION) STACK_NAME=$(STACK_NAME) S3_BUCKET=$(S3_BUCKET) AWS_VAULT=$(AWS_VAULT)"

# guard-FOO fails if variable FOO is empty
guard-%:
	@if [ -z "$($*)" ]; then \
		echo "ERROR: '$*' is required, e.g. make $(MAKECMDGOALS) $*=<value>"; \
		exit 1; \
	fi

# ---------------------------------------------------------------------------
# Build / validate
# ---------------------------------------------------------------------------
.PHONY: validate
validate: ## Lint & validate the SAM template
	$(RUN) sam validate --lint --region $(REGION)

.PHONY: build
build: ## Build with a container (needs Docker; builds Lambda-native wheels)
	$(RUN) sam build --use-container

# ---------------------------------------------------------------------------
# Publish to SAR
# ---------------------------------------------------------------------------
.PHONY: create-bucket
create-bucket: guard-S3_BUCKET ## Create the SAR artifact bucket + grant SAR read access
	$(RUN) aws s3 mb s3://$(S3_BUCKET) --region $(REGION) || true
	@ACCOUNT_ID=$$($(RUN) aws sts get-caller-identity --query Account --output text); \
	echo "Applying SAR read policy for account $$ACCOUNT_ID..."; \
	$(RUN) aws s3api put-bucket-policy --bucket $(S3_BUCKET) --policy \
	  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"serverlessrepo.amazonaws.com\"},\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$(S3_BUCKET)/*\",\"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$$ACCOUNT_ID\"}}}]}"

.PHONY: package
package: guard-S3_BUCKET ## Upload code artifacts to S3 and emit packaged.yaml
	$(RUN) sam package --template-file $(TEMPLATE) --output-template-file $(PACKAGED) \
		--s3-bucket $(S3_BUCKET) --region $(REGION)

.PHONY: publish
publish: ## Publish packaged.yaml to the Serverless Application Repository
	$(RUN) sam publish --template $(PACKAGED) --region $(REGION)
	@echo "Published. Bump SemanticVersion in $(TEMPLATE) before the next publish,"
	@echo "then mark the app public/shared in the SAR console."

.PHONY: release
release: validate build package publish ## Full pipeline: validate -> build -> package -> publish

# ---------------------------------------------------------------------------
# Local test deploy (into your own account) — NOT the SAR listing
# ---------------------------------------------------------------------------
.PHONY: deploy
deploy: build ## Guided deploy into your account for testing (prompts for params)
	$(RUN) sam deploy --guided --stack-name $(STACK_NAME) --region $(REGION) \
		--capabilities CAPABILITY_IAM

.PHONY: outputs
outputs: ## Show deployed stack outputs
	$(RUN) aws cloudformation describe-stacks --stack-name $(STACK_NAME) \
		--region $(REGION) --query 'Stacks[0].Outputs' --output table

.PHONY: logs-forwarder
logs-forwarder: ## Tail the OpenSearch forwarder logs
	$(RUN) sam logs --stack-name $(STACK_NAME) --name OpenSearchForwarderFunction \
		--region $(REGION) --tail

.PHONY: logs-digest
logs-digest: ## Tail the alert digest logs
	$(RUN) sam logs --stack-name $(STACK_NAME) --name AlertDigestFunction \
		--region $(REGION) --tail

.PHONY: destroy
destroy: ## Delete the test stack from your account
	$(RUN) sam delete --stack-name $(STACK_NAME) --region $(REGION)

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
.PHONY: clean
clean: ## Remove build artifacts (.aws-sam, packaged.yaml)
	rm -rf .aws-sam $(PACKAGED)
