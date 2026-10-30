FROM python:3.12-alpine

RUN apk add --no-cache poppler-utils tzdata

ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY app /app

CMD ["python3", "-u", "/app/main.py"]
