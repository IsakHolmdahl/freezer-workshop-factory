PYTHON ?= python3
VENV_PYTHON := .venv/bin/python
VENV_STAMP := .venv/.requirements-installed

.DEFAULT_GOAL := help
.PHONY: help venv start setup setup-auto setup-dry-run setup-services setup-scion setup-cli setup-images \
        setup-start setup-github setup-github-rotate setup-publish setup-copilot \
        doctor doctor-quick doctor-fix cleanup cleanup-dry-run cleanup-auto cleanup-volumes test

help:
	@echo 'make setup                 Guided installation (creates .venv automatically)'
	@echo 'make start                 Start or update everything: services (rebuilt when changed), Hub and broker'
	@echo 'make setup-auto            Unattended installation (accept defaults)'
	@echo 'make setup-dry-run         Preview installation'
	@echo 'make setup-services        Start MLflow, Dolt and telemetry; check the model endpoints'
	@echo 'make setup-copilot         Log MLflow in to GitHub Copilot (browser, pasted token or editor login)'
	@echo 'make setup-scion           Choose CLI location and build Scion'
	@echo 'make setup-cli             Install/update CLI shim and helpers without building Scion'
	@echo 'make setup-images          Build the OpenCode agent images'
	@echo 'make setup-start           Write Scion settings and start the local Hub and broker'
	@echo 'make setup-github          Configure agent PAT and publish Hub settings'
	@echo 'make setup-github-rotate   Replace agent PAT and publish Hub settings'
	@echo 'make setup-publish         Publish Hub settings, secrets, roles and skills'
	@echo 'make doctor                Check the installation'
	@echo 'make doctor-quick          Check without model calls'
	@echo 'make doctor-fix            Apply safe repairs and check again'
	@echo 'make cleanup               Interactive cleanup'
	@echo 'make cleanup-dry-run       Preview cleanup'
	@echo 'make cleanup-auto          Cleanup without prompts, retaining data volumes'
	@echo 'make cleanup-volumes       Cleanup without prompts, INCLUDING DATA VOLUMES'
	@echo 'make test                  Run the Python tests'
	@echo 'make venv                  Prepare or update the Python environment only'
	@echo 'Linux, macOS and Windows (WSL2: run .\\setup.ps1 in PowerShell first).'
	@echo 'Choose Python with PYTHON=python3.12. See docs/setup-steps.md for standalone steps.'

ifeq ($(shell command -v "$(PYTHON)" >/dev/null 2>&1 || test -x "$(VENV_PYTHON)"; echo $$?),0)
venv:
	@if [ ! -x "$(VENV_PYTHON)" ] || [ ! -f "$(VENV_STAMP)" ] || \
	    [ scripts/requirements.txt -nt "$(VENV_STAMP)" ] || [ setup.py -nt "$(VENV_STAMP)" ]; then \
	    if command -v "$(PYTHON)" >/dev/null 2>&1; then bootstrap="$(PYTHON)"; else bootstrap="$(VENV_PYTHON)"; fi; \
	    "$$bootstrap" setup.py --venv-only && touch "$(VENV_STAMP)"; \
	fi

setup: venv
	@"$(VENV_PYTHON)" scripts/setup.py

start: venv
	@"$(VENV_PYTHON)" scripts/steps/services.py --force
	@"$(VENV_PYTHON)" scripts/setup.py --only scion_settings,scion_start

setup-auto: venv
	@"$(VENV_PYTHON)" scripts/setup.py --yes

setup-dry-run: venv
	@"$(VENV_PYTHON)" scripts/setup.py --yes --dry-run

setup-services: venv
	@"$(VENV_PYTHON)" scripts/setup.py --only configuration,services,copilot,endpoints

setup-copilot: venv
	@"$(VENV_PYTHON)" scripts/steps/copilot.py $(COPILOT_ARGS)

setup-start: venv
	@"$(VENV_PYTHON)" scripts/setup.py --only scion_settings,scion_start

setup-scion: venv
	@"$(VENV_PYTHON)" scripts/setup.py --only scion_cli,build_scion

setup-cli: venv
	@"$(VENV_PYTHON)" scripts/scion_install.py

setup-images: venv
	@"$(VENV_PYTHON)" scripts/setup.py --only build_images

setup-github: venv
	@"$(VENV_PYTHON)" scripts/setup.py --only secrets,hub_wiring

setup-github-rotate: venv
	@"$(VENV_PYTHON)" scripts/steps/secrets.py --replace
	@"$(VENV_PYTHON)" scripts/setup.py --only hub_wiring

setup-publish: venv
	@"$(VENV_PYTHON)" scripts/setup.py --only hub_wiring

doctor: venv
	@"$(VENV_PYTHON)" scripts/doctor.py

doctor-quick: venv
	@"$(VENV_PYTHON)" scripts/doctor.py --quick

doctor-fix: venv
	@"$(VENV_PYTHON)" scripts/doctor.py --fix

cleanup: venv
	@"$(VENV_PYTHON)" scripts/cleanup.py

cleanup-dry-run: venv
	@"$(VENV_PYTHON)" scripts/cleanup.py --dry-run

cleanup-auto: venv
	@"$(VENV_PYTHON)" scripts/cleanup.py --yes

cleanup-volumes: venv
	@"$(VENV_PYTHON)" scripts/cleanup.py --yes --volumes

test: venv
	@"$(VENV_PYTHON)" -m pip install --quiet --disable-pip-version-check -r scripts/requirements-dev.txt
	@"$(VENV_PYTHON)" -m pytest
else
venv start setup setup-auto setup-dry-run setup-services setup-scion setup-cli setup-images \
setup-start setup-github setup-github-rotate setup-publish setup-copilot \
doctor doctor-quick doctor-fix cleanup cleanup-dry-run cleanup-auto cleanup-volumes test:
	@sh scripts/docker-python.sh "$@"
endif
