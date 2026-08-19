FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=4018
ENV SEO_DB=/data/opengsc/prod.db
ENV RESEARCH_DB=/data/audit/research.db
ENV REPORTS_DIR=/data/reports

EXPOSE 4018
CMD ["python", "app.py"]
