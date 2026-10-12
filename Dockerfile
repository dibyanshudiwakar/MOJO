FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
USER 10001
ENV PORT=8080 FACTORY_DB=/data/factory-v2.db
EXPOSE 8080
CMD ["python","-m","app.server"]
