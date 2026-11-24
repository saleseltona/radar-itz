"""
Ponto de entrada do Aplicativo Web - Radar de Pautas de Imperatriz (MA).
Compatível com hospedagem local e nuvem (Render, Railway, Fly.io, Heroku, etc).
"""
import os
from src.web_server import run_web_server

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Iniciando Radar de Pautas Web em http://localhost:{port}")
    run_web_server(host=host, port=port)
