FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.yaml ./config.yaml

ENV PYTHONPATH=/app/src
# state (active watches) persists here — mount a volume in production
RUN mkdir -p /app/state
EXPOSE 8000

# the web chat UI + API + 30-min scheduler, all in one process
CMD ["uvicorn", "appointment_bot.webapp:app", "--host", "0.0.0.0", "--port", "8000"]
