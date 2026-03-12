FROM python:3.12-slim

WORKDIR /app

COPY app.py /app/app.py

ENV MOCK_PORT=8000

EXPOSE 8000

CMD ["python", "/app/app.py"]
