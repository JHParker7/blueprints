FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm
WORKDIR /tests/integration_tests
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENTRYPOINT ["pytest"]
CMD ["-vv"]
