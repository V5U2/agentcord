FROM python:3.13-slim

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY *.py config-example.yaml README.md LICENSE.md ./

RUN mkdir -p /app/data/memory

CMD ["python", "llmcord.py"]
