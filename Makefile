# SkillStars: python pipeline -> static JSON -> static site (site_dist/) on GitHub Pages.
PY ?= python3
PORT ?= 8000

.PHONY: help install update discover enrich classify snapshot build serve test clean

help:
	@echo "make install    install Python deps (requests)"
	@echo "make update     full refresh: discover, enrich, classify, snapshot, build (keeps old data on failure)"
	@echo "make build      rebuild site_dist/ from data/ only"
	@echo "make serve      serve site_dist/ on http://localhost:$(PORT)/"
	@echo "make test       run unit tests"
	@echo "steps: make discover | enrich | classify | snapshot"

install:
	$(PY) -m pip install -r requirements.txt

update:
	bash deploy/update.sh && $(MAKE) build

discover:
	$(PY) pipeline/discover.py
enrich:
	$(PY) pipeline/enrich.py
classify:
	$(PY) pipeline/classify.py
snapshot:
	$(PY) pipeline/snapshot.py
build:
	$(PY) pipeline/build_site_data.py

serve:
	@echo "SkillStars on http://localhost:$(PORT)/"
	$(PY) -m http.server $(PORT) -d site_dist

test:
	$(PY) -m unittest discover -s pipeline/tests

clean:
	rm -rf site_dist site_dist.tmp .cache/check_site .cache/data-backup.tgz
