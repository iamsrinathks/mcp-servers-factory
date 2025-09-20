FROM python:3.13-slim

# Install dependencies
RUN pip install --no-cache-dir uv uvicorn fastapi requests mcp


WORKDIR /app

# Copy code
COPY main.py ./

ENV PYTHONUNBUFFERED=1

# Cloud Run default port
EXPOSE 8080

# Use Cloud Run's PORT env var
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
