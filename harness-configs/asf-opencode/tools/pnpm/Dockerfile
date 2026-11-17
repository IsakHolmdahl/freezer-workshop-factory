# Tool layer: pnpm 12.11.2 on the image's Node, from the npm registry with an
# integrity check. Applied by scripts/build-harness.py.
# provides: pnpm
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG PNPM_VERSION=12.11.2
ARG PNPM_INTEGRITY=sha512-JaUWUqGWeFr18ovabf6NxYLr76SsHRgCIc4TkCMcX7dmN4IupLbkYzznSs3LCnf3Ja8RutOBd/3t2u6BipVcuQ==
RUN cd /tmp \
    && npm pack "pnpm@${PNPM_VERSION}" --silent > /tmp/pnpm-pack.txt \
    && archive="$(tail -n1 /tmp/pnpm-pack.txt)" \
    && actual="sha512-$(openssl dgst -sha512 -binary "$archive" | base64 -w0)" \
    && [ "$actual" = "$PNPM_INTEGRITY" ] \
    && npm install --global --prefix /usr/local "/tmp/$archive" \
    && rm -f "/tmp/$archive" /tmp/pnpm-pack.txt \
    && pnpm --version
COPY TOOLS.md /etc/scion-tools/pnpm.md
