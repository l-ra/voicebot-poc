FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/l-ra/voicebot-poc"
ARG APP_VERSION=local
LABEL org.opencontainers.image.version=$APP_VERSION
ENV APP_VERSION=$APP_VERSION
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py quiz.py settings.py audit.py questions.json index.html ./
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BIND_HOST=0.0.0.0 PORT=8765 VOICEBOT_CONFIG=/run/voicebot/config.json
EXPOSE 8765
CMD ["python", "server.py"]
