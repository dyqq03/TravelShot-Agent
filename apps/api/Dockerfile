FROM python:3.12-slim

WORKDIR /app

COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
RUN pip install --no-cache-dir .

COPY db/schema.sql ./db/schema.sql
COPY db/seed ./db/seed

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
