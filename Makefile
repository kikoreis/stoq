TEST_MODULES=stoqlib tests

# http://stackoverflow.com/questions/2214575/passing-arguments-to-make-run
# List of command that takes test_modules arguments via make
TEST_MODULES_CMD=check check-failed
ifneq (,$(findstring $(firstword $(MAKECMDGOALS)),$(TEST_MODULES_CMD)))
  _TEST_ARGS=$(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(_TEST_ARGS):;@:)
  ifneq (,$(_TEST_ARGS))
    TEST_MODULES=$(_TEST_ARGS)
  endif
else
endif

howto:
	make -C docs/howto html

apidocs:
	make -C docs/api html

manual:
	mkdir -p docs/manual/pt_BR/_build/html
	yelp-build html -o docs/manual/pt_BR/_build/html docs/manual/pt_BR

lint-diff-only:
	git diff --name-only --diff-filter=ACM HEAD | grep "*.py" | xargs pyflakes
	git diff --name-only --diff-filter=ACM HEAD | grep "*.py" | xargs pycodestyle

lint:
	pyflakes $(TEST_MODULES)
	pycodestyle $(TEST_MODULES)

check: clean lint-diff-only
	@echo "Running pytest"
	pytest

coverage: clean lint
	pytest --cov=stoqlib/ --cov-report=xml
	coverage xml --omit "**/test/*.py,stoqlib/pytests/*"
	utils/validatecoverage.py coverage.xml
	PYTHONIOENCODING=utf8 git show | python3 utils/diff-coverage coverage.xml

test:
	pytest

include utils/utils.mk
.PHONY: dist deb wheel debsource wheel-upload
.PHONY: clean clean-eggs clean-build clean-docs
