FROM python:3.11-slim

WORKDIR /app

# System deps needed by psycopg2-binary / general builds, plus the
# runtime shared libs opencv-python-headless (pulled in by easyocr) and
# torch occasionally still dlopen even in "headless" mode.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libpq-dev libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
