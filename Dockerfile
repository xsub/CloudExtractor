FROM docker.io/library/python:3.13-slim AS base

# Use the pure Python wheel to avoid the self-extracting binary and its temporary files.
RUN python -m pip install --no-cache-dir \
    'icloudpd @ https://files.pythonhosted.org/packages/d8/55/6ebee6620714b548befcf45c4873bafaa8b5a57d25c79d40ce947f1c4e04/icloudpd-1.33.0-py3-none-any.whl#sha256=3ff27038ebc17105a5c75a4202e938d4485d80880f8e88b1b4f299e9edc000b8'

WORKDIR /opt/cloudextractor

ENV HOME=/state \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

ENTRYPOINT ["python3", "-m", "cloudextractor"]

FROM base AS worker
COPY cloudextractor/ ./cloudextractor/
USER 1000:1000
CMD ["run"]

FROM base AS dashboard
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir Pillow==13.0.0 pillow-heif==1.8.1
COPY cloudextractor/ ./cloudextractor/
USER 1000:1000
CMD ["ui"]
