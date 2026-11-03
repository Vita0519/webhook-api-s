FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn aiohttp python-multipart pydantic

COPY app/ ./app/

EXPOSE 8100

ENV WEBHOOK_API_KEY=webhook-default-key-change-me
ENV WEBHOOK_POLL_INTERVAL=2
ENV WEBHOOK_MSG_RETENTION=7

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
