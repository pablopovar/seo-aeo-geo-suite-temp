FROM node:24-bookworm-slim AS node-runtime

FROM python:3.13-slim-bookworm

# Node 24 runtime + npm/npx, plus Python build/runtime tools and supervisor.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      supervisor build-essential openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /suite

# Keep each application isolated inside the single container.
COPY auditor-seo-repo/ /suite/opengsc/
COPY auditor-dashboard/ /suite/dashboard/
COPY auditor-aeo-geo-repo/ /suite/aeo-geo-auditor/
COPY suite/ /suite/runtime/

# OpenGSC dependencies + production build.
WORKDIR /suite/opengsc
ENV DATABASE_URL=file:/tmp/opengsc-build.db
RUN npm ci && npm run build

# Isolated Python environments for the two Python apps.
RUN python -m venv /opt/venvs/dashboard \
 && /opt/venvs/dashboard/bin/pip install --no-cache-dir -r /suite/dashboard/requirements.txt \
 && python -m venv /opt/venvs/aeo \
 && /opt/venvs/aeo/bin/pip install --no-cache-dir -r /suite/aeo-geo-auditor/requirements.txt

RUN chmod +x /suite/runtime/entrypoint.sh \
 && mkdir -p /suite/data/opengsc /suite/data/dashboard /suite/reports

WORKDIR /suite
EXPOSE 4017 4018 4019
ENTRYPOINT ["/suite/runtime/entrypoint.sh"]
