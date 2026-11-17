FROM ghcr.io/mlflow/mlflow:latest-full

# Install custom packages while keeping the base image's exact MLflow version
RUN MLFLOW_VER=$(python -c "import mlflow; print(mlflow.__version__)") && \
  pip install --no-cache-dir litellm mcp "mlflow[genai]==${MLFLOW_VER}" psycopg2-binary boto3

COPY bootstrap/ /opt/mlflow-bootstrap/
RUN mkdir -p /var/lib/litellm-copilot && chmod 0777 /var/lib/litellm-copilot

ENV PYTHONPATH=/opt/mlflow-bootstrap \
  GITHUB_COPILOT_TOKEN_DIR=/var/lib/litellm-copilot

# Fail the build if a LiteLLM / Copilot patch stops applying
RUN python /opt/mlflow-bootstrap/verify_runtime.py
