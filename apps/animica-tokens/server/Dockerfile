FROM node:20-bullseye

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /repo

COPY sdk/python /repo/sdk/python
COPY contracts /repo/contracts
COPY scripts/animica_tokens /repo/scripts/animica_tokens
COPY apps/animica-tokens /repo/apps/animica-tokens

RUN pip3 install -e /repo/sdk/python
RUN npm --prefix /repo/apps/animica-tokens install

ENV PYTHONPATH=/repo:/repo/sdk/python
WORKDIR /repo/apps/animica-tokens

EXPOSE 8787
CMD ["node", "server/index.mjs"]
