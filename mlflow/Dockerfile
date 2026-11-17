FROM ghcr.io/mlflow/mlflow:latest-full

# Install additional custom packages
RUN pip install --no-cache-dir litellm mcp "mlflow[genai]" psycopg2-binary boto3

COPY bootstrap/ /opt/mlflow-bootstrap/
ENV PYTHONPATH=/opt/mlflow-bootstrap
RUN python -c 'import mcp, gateway_session'
