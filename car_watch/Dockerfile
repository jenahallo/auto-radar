FROM python:3.12-slim
WORKDIR /app
RUN useradd --uid 10001 --create-home carwatch
COPY --chown=carwatch:carwatch . /app
RUN mkdir -p /app/data && chown carwatch:carwatch /app/data
USER carwatch
ENV PYTHONUNBUFFERED=1 CAR_HOST=0.0.0.0 PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"
CMD ["python", "server.py"]
