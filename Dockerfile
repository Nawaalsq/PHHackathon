# Hugging Face Spaces deployment: multi-stage build.
# Stage 1 builds the React frontend; stage 2 runs the FastAPI backend,
# which serves the built frontend and the JSON API on one origin.
# HF Spaces expects the app to listen on port 7860.

# --- Stage 1: frontend build ---
FROM node:20-slim AS webbuild
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Stage 2: backend ---
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rd_engine/ rd_engine/
COPY clean/sample1_dev_seeded/ clean/sample1_dev_seeded/
COPY results/sample1_dev_seeded/ results/sample1_dev_seeded/
COPY answer_keys/ answer_keys/
COPY --from=webbuild /app/web/dist web/dist

# The HF Space serves the frontend itself (same origin, no CORS needed);
# also allow the Vercel frontend origin so both sites work.
ENV CORS_ORIGINS="https://daman-claim-decision-engine.vercel.app"

EXPOSE 7860
CMD ["uvicorn", "rd_engine.api:app", "--host", "0.0.0.0", "--port", "7860"]
