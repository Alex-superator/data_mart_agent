FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN python -m unittest discover -s tests
CMD ["python", "-m", "dmharness", "run", "examples/feature_request.md", "--out", "/app/out"]
