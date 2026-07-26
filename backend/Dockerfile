# Render's Docker runtime injects $PORT and expects the app to bind to it;
# default 7860 keeps local/manual runs working the same way.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 7860
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}
