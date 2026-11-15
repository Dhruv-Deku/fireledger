# FireLedger API, grid-only copy for Hugging Face Spaces (Docker SDK).
# Built by pipeline/make_space.py, which copies api/ and the derived data next to this file.
FROM python:3.10-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user api/ api/
COPY --chown=user data/ data/

EXPOSE 7860
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
