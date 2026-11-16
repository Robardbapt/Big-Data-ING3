FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY construct_ai ./construct_ai
COPY web ./web
ENV PYTHONUNBUFFERED=1 CONSTRUCT_LAKE=/data/lake
EXPOSE 8000
ENTRYPOINT ["python", "-m", "construct_ai"]
CMD ["demo"]
