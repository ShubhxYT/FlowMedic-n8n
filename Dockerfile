FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /service
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home flowmedic \
    && mkdir /data && chown flowmedic:flowmedic /data
USER flowmedic
ENV DATABASE_URL=sqlite:////data/flowmedic.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
