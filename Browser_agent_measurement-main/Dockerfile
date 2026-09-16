# Reproduction container for the ACSAC 2026 artifact
#   "Brave New Browsing! Tracker Exposure under Browser-Agent Delegation"
#
# Reproduces every table and figure from the shipped per-session data.
# No GPU, no proxy, no API keys. The only step that needs the network is the
# pip install during build; the container run itself is fully offline.
#
#   docker build -t brave-new-browsing .
#   docker run --rm --network none brave-new-browsing
#
# To run a single claim instead of everything:
#   docker run --rm --network none brave-new-browsing \
#       bash claims/01_matched_exposure/run.sh
#
# Outputs are rewritten in place inside the container. To keep them, run a
# named container and copy them out:
#   docker run --name bnb --network none brave-new-browsing
#   docker cp bnb:/ae/artifact/rq1_human_vs_agent/expected_outputs ./out-rq1
#   docker rm bnb

FROM python:3.12-slim-bookworm

# Headless matplotlib; unbuffered logs so the evaluator sees progress live.
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /ae

# Dependency layer first so a repository edit does not re-run pip.
COPY artifact/requirements-repro.txt ./artifact/
RUN pip install --no-cache-dir -r artifact/requirements-repro.txt

COPY . .

# REPRODUCE.sh regenerates every table and figure; verify.py compares the
# result against the shipped reference values and exits non-zero on any
# deviation beyond tolerance.
CMD ["bash", "-c", "bash artifact/REPRODUCE.sh && python artifact/verify.py"]
