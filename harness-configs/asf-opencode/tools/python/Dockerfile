# Tool layer: Python 3 toolchain (`python` alias, pip and venv).
# Applied on top of an asf-opencode image by scripts/build-harness.py.
# provides: python pip pip3
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python-is-python3 \
    && rm -rf /var/lib/apt/lists/* \
    && python --version && pip --version && pip3 --version \
    && python -m venv /tmp/python-venv-check && rm -rf /tmp/python-venv-check
COPY TOOLS.md /etc/scion-tools/python.md
