# Dockerfile para o Radar de Pautas de Imperatriz (MA)
FROM python:3.11-slim

WORKDIR /app

# Copia os arquivos do projeto
COPY . /app

# Dependências (só a criptografia das notificações push)
RUN pip install --no-cache-dir -r requirements.txt

# Porta configurável (padrão 8000 ou variável de ambiente PORT)
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0

# Executa o servidor web do Radar
CMD ["python", "app.py"]
