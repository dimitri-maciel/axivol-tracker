FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS ingest
COPY hcclab/__init__.py hcclab/tcia.py hcclab/model_assets.py ./hcclab/
ENTRYPOINT ["python", "-m", "hcclab.tcia"]

FROM base AS app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY requirements-model.txt .
RUN pip install --no-cache-dir -r requirements-model.txt \
    && SAM2_BUILD_CUDA=0 pip install --no-cache-dir --no-deps --no-build-isolation \
       https://github.com/bowang-lab/MedSAM2/archive/332f30d420f1d1b08e2a79b3ae6a602458808383.tar.gz
COPY hcclab/model_assets.py /tmp/model_assets.py
RUN python /tmp/model_assets.py install-config && rm /tmp/model_assets.py
COPY hcclab ./hcclab
COPY web ./web
COPY tests ./tests
RUN useradd --system --uid 10001 --no-create-home lab \
    && mkdir /annotations && chown lab:lab /annotations
USER lab
EXPOSE 8000
CMD ["python", "-m", "hcclab.server", "--data", "/data", "--host", "0.0.0.0"]
