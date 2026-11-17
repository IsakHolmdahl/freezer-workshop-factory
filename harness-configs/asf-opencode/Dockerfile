# Set by scripts/build-harness.py / scripts/ci-images.py (a scion-base image).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG OPENCODE_VERSION=1.19.5
ARG AST_GREP_VERSION=0.51.1
ARG BD_VERSION=0.62.0
ARG BD_SHA256_AMD64=4cca7265b22e5c3ca8d62ab0b9752bec31f68b7f5fa636282a4c7e5454c35535
ARG BD_SHA256_ARM64=c6ba2a4aae150db3085984bd37b090c84b2d1beeac32472ee001bd203ad7c7db
ARG DOLT_VERSION=2.0.7
ARG DOLT_SHA256_AMD64=8e09138596763896cbce0632cd6e779fa1df405334c6c148bc314539a3b9795a
ARG DOLT_SHA256_ARM64=ebc6352c00bbdd4b1980c3fd7072ee6dde03b85cb8c1417ff1468af0d0d52cc4
ARG RTK_VERSION=1.0.0
ARG RTK_SHA256_AMD64=9a0f4f8240a0701d9c409dcd9afc1f6f1e8625d55299d84af17fcd26d0b70600
ARG RTK_SHA256_ARM64=318d1b3b8438a260762f2a5b050bd5a61ebaa7bec13f37c475ca1d6372a3b61d

USER root
# ripgrep and fd are OpenCode's file search tools (Debian names the fd binary fdfind).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 fd-find ripgrep \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# Docker Desktop shows the bind-mounted /workspace as owned by root, so git
# (as the agent user) distrusts a repo there and sciontool's clone fails with
# "--stdin requires a git repository". System config, since HOME is a mount.
RUN git config --system --add safe.directory /workspace

RUN mkdir -p /home/scion/.config/opencode /home/scion/.local/share/opencode /home/scion/.cache && \
  chown -R scion:scion /home/scion/.config /home/scion/.local /home/scion/.cache

# Install pinned bd, dolt, and rtk binaries (SHA-256 verified per architecture).
RUN set -eu; cd /tmp \
    && case "$(uname -m)" in \
         x86_64) \
           ARCH="amd64"; \
           BD_SHA256="${BD_SHA256_AMD64}"; \
           DOLT_SHA256="${DOLT_SHA256_AMD64}"; \
           RTK_TRIPLE="x86_64-unknown-linux-musl"; \
           RTK_SHA256="${RTK_SHA256_AMD64}" ;; \
         aarch64|arm64) \
           ARCH="arm64"; \
           BD_SHA256="${BD_SHA256_ARM64}"; \
           DOLT_SHA256="${DOLT_SHA256_ARM64}"; \
           RTK_TRIPLE="aarch64-unknown-linux-gnu"; \
           RTK_SHA256="${RTK_SHA256_ARM64}" ;; \
         *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;; \
       esac \
    && curl -fsSLo bd.tgz "https://github.com/gastownhall/beads/releases/download/v${BD_VERSION}/beads_${BD_VERSION}_linux_${ARCH}.tar.gz" \
    && echo "${BD_SHA256}  bd.tgz" | sha256sum -c - \
    && tar xzf bd.tgz bd && install -m 755 bd /usr/local/bin/bd && rm -f bd.tgz bd \
    && curl -fsSLo dolt.tgz "https://github.com/dolthub/dolt/releases/download/v${DOLT_VERSION}/dolt-linux-${ARCH}.tar.gz" \
    && echo "${DOLT_SHA256}  dolt.tgz" | sha256sum -c - \
    && tar xzf dolt.tgz "dolt-linux-${ARCH}/bin/dolt" && install -m 755 "dolt-linux-${ARCH}/bin/dolt" /usr/local/bin/dolt && rm -rf dolt.tgz "dolt-linux-${ARCH}" \
    && curl -fsSLo rtk.tgz "https://github.com/rtk-ai/rtk/releases/download/v${RTK_VERSION}/rtk-oss-${RTK_TRIPLE}.tar.gz" \
    && echo "${RTK_SHA256}  rtk.tgz" | sha256sum -c - \
    && tar xzf rtk.tgz rtk && install -m 755 rtk /usr/local/bin/rtk && rm -f rtk.tgz rtk \
    && bd version && dolt version && rtk --version | grep -qx "rtk ${RTK_VERSION}"

# Agent-facing tool notes; tool layers add /etc/scion-tools/<tool>.md.
COPY image/TOOLS.md /etc/scion-tools/00-base.md

# OpenCode (pinned) and ast-grep, on the base image's Node.
RUN npm install -g opencode-ai@${OPENCODE_VERSION} @ast-grep/cli@${AST_GREP_VERSION} \
    && opencode --version && ast-grep --version \
    && npm cache clean --force

# sciontool init (provisioning, hooks) -> asf-entrypoint (Beads/trace env) -> opencode.
COPY --chmod=755 asf-entrypoint.py /usr/local/bin/asf-entrypoint
ENTRYPOINT ["sciontool", "init", "--", "/usr/local/bin/asf-entrypoint"]
WORKDIR /workspace
