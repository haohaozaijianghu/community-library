FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY web ./web
COPY library ./library
COPY fixtures ./fixtures
COPY scripts/seed_demo_books.py ./scripts/seed_demo_books.py
COPY prompt.md ./prompt.md
