# repo-brief.mk — the commands every consuming repository shares.
#
# Include this at the *bottom* of the repo Makefile, after the repo's own
# variables and targets:
#
#   REPO_BRIEF ?= tools/vendor/repo-brief
#   -include $(REPO_BRIEF)/make/repo-brief.mk
#
# Bottom matters: in GNU make the last definition wins, so these targets own
# their names. Anything set before the include (`PYTHON`, `REPO_BRIEF`, `ARGS`)
# overrides the defaults here.
#
#   make index                     write .repo-brief/map.md
#   make index-check               fail if its structure is stale
#   make init                      set the repo up: adapter, index, map, overview, tools
#   make update                    refresh all of it, re-reading only changed files
#   make trust                     allow this repo's own scripts to run
#   make serve                     MCP server for this repo (needs repo-brief[mcp])
#   make context q="<task>"        rank code, docs, decisions and open work
#   make context ARGS=--brief q="<task>"
#   make graph ARGS="--task <q>"   dependency view; `graph <area>` via ARGS
#   make preflight                 map check plus the repo's own checks
#   make archive [ARGS=...]        review, or move explicit paths
#   make writing [ARGS=--force]    rewrite docs/meta/writing.md
#   make skill [ARGS=--check]      install, or verify, the shipped skills

REPO_BRIEF ?= tools/vendor/repo-brief
PYTHON ?= python3
BRIEF = PYTHONPATH=$(REPO_BRIEF) $(PYTHON) -m repobrief.cli --root .

.PHONY: index index-check init update trust serve context graph preflight archive writing skill

index:
	$(BRIEF) index

index-check:
	$(BRIEF) index --check

init:
	$(BRIEF) init $(ARGS)

update:
	$(BRIEF) update $(ARGS)

trust:
	$(BRIEF) trust $(ARGS)

serve:
	$(BRIEF) serve $(ARGS)

context:
	@test -n "$(q)" || { echo 'usage: make context q="<task>"'; exit 2; }
	$(BRIEF) context $(ARGS) "$(q)"

graph:
	$(BRIEF) graph $(ARGS)

preflight:
	$(BRIEF) index --check
	$(BRIEF) preflight

archive:
	$(BRIEF) archive $(ARGS)

writing:
	$(BRIEF) writing $(ARGS)

skill:
	$(BRIEF) skill $(ARGS)
