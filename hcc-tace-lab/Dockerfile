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
COPY experiments ./experiments
COPY docs/research/annotation-kit.md docs/research/reader-form.example.json ./docs/research/
RUN useradd --system --uid 10001 --no-create-home lab \
    && mkdir /annotations && chown lab:lab /annotations
USER lab
EXPOSE 8000
CMD ["python", "-m", "hcclab.server", "--data", "/data", "--host", "0.0.0.0"]

# Optional, isolated research environment; compose.yaml still targets `app`.
FROM app AS neural-research
USER root
COPY experiments/constraints-neural-research.txt /tmp/constraints-neural-research.txt
RUN pip install --no-cache-dir --constraint /tmp/constraints-neural-research.txt setuptools==84.2.1 \
    && pip install --no-cache-dir --no-build-isolation \
       --constraint /tmp/constraints-neural-research.txt \
       --report /usr/local/share/axivol-neural-install.json \
       'nninteractive-client @ https://codeload.github.com/MIC-DKFZ/nnInteractive/tar.gz/e3126a029f46dcfd798c8428eec292f52cd438d6#sha256=75e05e4c345bff997e28f7f770e937031446c61f4aa1ccf93b7f5864b1acb064&subdirectory=client' \
       'nnInteractive @ https://codeload.github.com/MIC-DKFZ/nnInteractive/tar.gz/e3126a029f46dcfd798c8428eec292f52cd438d6#sha256=75e05e4c345bff997e28f7f770e937031446c61f4aa1ccf93b7f5864b1acb064' \
    && pip check \
    && pip freeze --all > /usr/local/share/axivol-neural-freeze.txt \
    && python -c "from experiments.nninteractive_refine import _verify_source; print(_verify_source())" \
    && rm /tmp/constraints-neural-research.txt
USER lab
