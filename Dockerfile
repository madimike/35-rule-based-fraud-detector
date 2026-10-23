
# Stage 1: Build the React frontend
FROM node:22.12.0-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run lint && npm run build


# Stage 2: Run the FastAPI backend
FROM python:3.13-slim AS backend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app/ ./app/

RUN pip install --no-cache-dir .

# FastAPI serves the compiled React frontend from this directory.
COPY --from=frontend-build /frontend/dist ./frontend/dist

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
