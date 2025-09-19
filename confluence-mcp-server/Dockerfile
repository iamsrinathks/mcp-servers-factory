FROM python:3.13-slim

RUN pip install --no-cache-dir uv uvicorn fastapi requests mcp

WORKDIR /app

COPY main.py ./

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Bind to Cloud Run's PORT (fallback to 8080)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
