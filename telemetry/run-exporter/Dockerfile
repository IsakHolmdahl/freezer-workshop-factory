# Prometheus exporter for factory runs (Beads epics). See TELEMETRY.md.
FROM python:3.12-slim
WORKDIR /opt/run-exporter
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY runs.py exporter.py ./
USER nobody
EXPOSE 9105
ENTRYPOINT ["python", "exporter.py"]
