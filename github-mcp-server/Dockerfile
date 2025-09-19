FROM python:3.13-slim

# Install dependencies
RUN pip install --no-cache-dir uv uvicorn fastapi requests mcp

WORKDIR /app

COPY main.py ./

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${PORT:-8080}"]
