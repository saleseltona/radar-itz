"""
Notificações push (Web Push) do Radar de Pautas: avisa o celular quando chegam pautas novas
ou contratos novos do PNCP.

Funciona com o painel instalado como app (PWA) no iPhone (iOS 16.4+, "Adicionar à Tela de
Início"), Android e navegadores de desktop. Não usa App Store nem conta de desenvolvedor Apple.

- Chave VAPID: só a privada fica no ambiente (`RADAR_VAPID_PRIVATE_KEY`, 32 bytes em base64url);
  a pública é derivada dela. `RADAR_VAPID_SUBJECT` identifica o remetente (padrão: URL do Render).
- Criptografia da mensagem: RFC 8291 (aes128gcm) e cabeçalho VAPID RFC 8292 (JWT ES256), com a
  biblioteca `cryptography` (única dependência externa; sem ela o push fica desligado).
- Inscrições: tabela `push_inscricoes` (SQLite local ou Supabase).
- Uma notificação por rodada de coleta (nunca uma por item), e o PNCP só avisa contratos novos
  na sincronização incremental: a carga histórica inicial não dispara aviso.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import mask_secrets

logger = logging.getLogger("radar_imperatriz.push")

VAPID_PRIVATE_ENV = "RADAR_VAPID_PRIVATE_KEY"
VAPID_SUBJECT_ENV = "RADAR_VAPID_SUBJECT"
VAPID_SUBJECT_PADRAO = "https://radar-itz-web.onrender.com"
TABELA = "push_inscricoes"
TTL_SEGUNDOS = 24 * 3600
HTTP_TIMEOUT = 10
MAX_INSCRICOES = 50
# Serviços de push aceitos (evita que o servidor seja usado para chamar URLs arbitrárias).
HOSTS_PUSH_PERMITIDOS = (
    "push.apple.com",
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)

# (método, url, headers, corpo, timeout) -> (status, corpo)
EnviarFn = Callable[[str, str, Dict[str, str], bytes, int], Tuple[int, bytes]]


class PushIndisponivel(RuntimeError):
    """Push desligado: falta a chave VAPID ou a biblioteca `cryptography`."""


# ---------------------------------------------------------------------------
# base64url
# ---------------------------------------------------------------------------
def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(texto: str) -> bytes:
    texto = str(texto or "").strip()
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


# ---------------------------------------------------------------------------
# Chaves VAPID
# ---------------------------------------------------------------------------
def _ec():
    try:
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise PushIndisponivel("Biblioteca 'cryptography' não instalada (pip install -r requirements.txt).") from exc
    return ec


def gerar_chave_privada_vapid() -> str:
    """Nova chave privada P-256 em base64url (32 bytes), para `RADAR_VAPID_PRIVATE_KEY`."""
    ec = _ec()
    chave = ec.generate_private_key(ec.SECP256R1())
    return b64url_encode(chave.private_numbers().private_value.to_bytes(32, "big"))


def _carregar_privada(b64: str):
    ec = _ec()
    raw = b64url_decode(b64)
    if len(raw) != 32:
        raise PushIndisponivel(f"{VAPID_PRIVATE_ENV} inválida: esperado 32 bytes em base64url.")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _publica_bruta(chave_publica) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return chave_publica.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def chave_privada_do_ambiente() -> str:
    return os.environ.get(VAPID_PRIVATE_ENV, "").strip()


def push_configurado() -> bool:
    if not chave_privada_do_ambiente():
        return False
    try:
        _ec()
    except PushIndisponivel:
        return False
    return True


def chave_publica_vapid(privada_b64: Optional[str] = None) -> str:
    """Chave pública (65 bytes, ponto não comprimido) em base64url, usada no navegador."""
    privada = _carregar_privada(privada_b64 or chave_privada_do_ambiente())
    return b64url_encode(_publica_bruta(privada.public_key()))


def cabecalho_vapid(endpoint: str, privada_b64: str, subject: str, agora: Optional[float] = None) -> str:
    """Valor do header Authorization: `vapid t=<JWT ES256>, k=<chave pública>` (RFC 8292)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    ec = _ec()
    privada = _carregar_privada(privada_b64)
    partes = urllib.parse.urlsplit(endpoint)
    aud = f"{partes.scheme}://{partes.netloc}"
    agora = time.time() if agora is None else agora
    cab = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    corpo = b64url_encode(
        json.dumps({"aud": aud, "exp": int(agora) + 12 * 3600, "sub": subject}, separators=(",", ":")).encode()
    )
    assinado = f"{cab}.{corpo}".encode("ascii")
    r, s = decode_dss_signature(privada.sign(assinado, ec.ECDSA(hashes.SHA256())))
    jwt = f"{cab}.{corpo}.{b64url_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={b64url_encode(_publica_bruta(privada.public_key()))}"


# ---------------------------------------------------------------------------
# Criptografia da mensagem (RFC 8291, aes128gcm)
# ---------------------------------------------------------------------------
def _hmac(chave: bytes, dados: bytes) -> bytes:
    return hmac.new(chave, dados, hashlib.sha256).digest()


def criptografar(
    mensagem: bytes,
    p256dh_b64: str,
    auth_b64: str,
    salt: Optional[bytes] = None,
    privada_efemera=None,
) -> bytes:
    """Corpo cifrado para o serviço de push. `salt`/`privada_efemera` só para testes."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ec = _ec()
    ua_publica = b64url_decode(p256dh_b64)
    auth = b64url_decode(auth_b64)
    if len(ua_publica) != 65 or len(auth) < 16:
        raise ValueError("Inscrição de push com chaves inválidas.")
    ua_chave = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_publica)
    efemera = privada_efemera or ec.generate_private_key(ec.SECP256R1())
    as_publica = _publica_bruta(efemera.public_key())
    segredo = efemera.exchange(ec.ECDH(), ua_chave)

    prk_chave = _hmac(auth, segredo)
    ikm = _hmac(prk_chave, b"WebPush: info\x00" + ua_publica + as_publica + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    cifrado = AESGCM(cek).encrypt(nonce, mensagem + b"\x02", None)
    return salt + struct.pack("!I", 4096) + bytes([len(as_publica)]) + as_publica + cifrado


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------
def endpoint_permitido(endpoint: str) -> bool:
    try:
        partes = urllib.parse.urlsplit(str(endpoint or ""))
    except ValueError:
        return False
    host = (partes.hostname or "").lower()
    if partes.scheme != "https" or not host:
        return False
    return any(host == h or host.endswith("." + h) for h in HOSTS_PUSH_PERMITIDOS)


def _enviar_urllib(metodo: str, url: str, headers: Dict[str, str], corpo: bytes, timeout: int) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=corpo, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host validado
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


def enviar_para_inscricao(
    inscricao: Dict[str, Any],
    payload: Dict[str, Any],
    privada_b64: Optional[str] = None,
    subject: Optional[str] = None,
    enviar: EnviarFn = _enviar_urllib,
) -> int:
    """Envia uma notificação para um aparelho. Devolve o status HTTP do serviço de push."""
    endpoint = str(inscricao.get("endpoint") or "")
    if not endpoint_permitido(endpoint):
        raise ValueError("Endpoint de push não permitido.")
    privada_b64 = privada_b64 or chave_privada_do_ambiente()
    if not privada_b64:
        raise PushIndisponivel(f"Defina {VAPID_PRIVATE_ENV} no servidor.")
    subject = subject or os.environ.get(VAPID_SUBJECT_ENV, "").strip() or VAPID_SUBJECT_PADRAO
    corpo = criptografar(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"), str(inscricao.get("p256dh")), str(inscricao.get("auth"))
    )
    headers = {
        "Authorization": cabecalho_vapid(endpoint, privada_b64, subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SEGUNDOS),
        "Urgency": "normal",
    }
    status, _ = enviar("POST", endpoint, headers, corpo, HTTP_TIMEOUT)
    return status


# ---------------------------------------------------------------------------
# Inscrições (SQLite ou Supabase)
# ---------------------------------------------------------------------------
_SQL_TABELA = f"""
CREATE TABLE IF NOT EXISTS {TABELA} (
    endpoint TEXT PRIMARY KEY,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT DEFAULT '',
    criado_em TEXT NOT NULL DEFAULT '',
    ultimo_envio TEXT DEFAULT '',
    ultimo_status INTEGER DEFAULT NULL
)
"""


def _eh_supabase(db: Any) -> bool:
    return getattr(db, "backend_name", "sqlite") == "supabase"


def validar_inscricao(dados: Any) -> Dict[str, str]:
    """Normaliza o JSON de `PushSubscription.toJSON()`. Lança ValueError se inválido."""
    if not isinstance(dados, dict):
        raise ValueError("Inscrição ausente.")
    endpoint = str(dados.get("endpoint") or "").strip()
    chaves = dados.get("keys") if isinstance(dados.get("keys"), dict) else {}
    p256dh = str(chaves.get("p256dh") or "").strip()
    auth = str(chaves.get("auth") or "").strip()
    if len(endpoint) > 1024 or not endpoint_permitido(endpoint):
        raise ValueError("Endpoint de push inválido ou de serviço não reconhecido.")
    try:
        if len(b64url_decode(p256dh)) != 65 or len(b64url_decode(auth)) < 16:
            raise ValueError
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Chaves da inscrição de push inválidas.") from exc
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


def salvar_inscricao(db: Any, inscricao: Dict[str, str], user_agent: str = "") -> None:
    linha = dict(inscricao, user_agent=str(user_agent or "")[:200], criado_em=datetime.now().isoformat(timespec="seconds"))
    if _eh_supabase(db):
        existe = db.client.select(
            TABELA, params={"select": "endpoint", "endpoint": f"eq.{linha['endpoint']}"}, fetch_all=False
        )
        if not existe and db.client.count(TABELA) >= MAX_INSCRICOES:
            raise ValueError("Limite de aparelhos inscritos atingido.")
        db.client.upsert(TABELA, [linha], on_conflict="endpoint")
        return
    with db.connect() as conn:
        conn.execute(_SQL_TABELA)
        total = conn.execute(f"SELECT COUNT(*) FROM {TABELA}").fetchone()[0]
        existe = conn.execute(f"SELECT 1 FROM {TABELA} WHERE endpoint = ?", (linha["endpoint"],)).fetchone()
        if not existe and total >= MAX_INSCRICOES:
            raise ValueError("Limite de aparelhos inscritos atingido.")
        conn.execute(
            f"""INSERT INTO {TABELA} (endpoint, p256dh, auth, user_agent, criado_em) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth,
                user_agent = excluded.user_agent""",
            (linha["endpoint"], linha["p256dh"], linha["auth"], linha["user_agent"], linha["criado_em"]),
        )


def remover_inscricao(db: Any, endpoint: str) -> None:
    if _eh_supabase(db):
        db.client.delete(TABELA, {"endpoint": f"eq.{endpoint}"})
        return
    with db.connect() as conn:
        conn.execute(_SQL_TABELA)
        conn.execute(f"DELETE FROM {TABELA} WHERE endpoint = ?", (endpoint,))


def listar_inscricoes(db: Any) -> List[Dict[str, Any]]:
    if _eh_supabase(db):
        return db.client.select(TABELA, params={"select": "endpoint,p256dh,auth"}, fetch_all=False)
    with db.connect() as conn:
        conn.execute(_SQL_TABELA)
        return [dict(r) for r in conn.execute(f"SELECT endpoint, p256dh, auth FROM {TABELA}").fetchall()]


def _registrar_status(db: Any, endpoint: str, status: int) -> None:
    agora = datetime.now().isoformat(timespec="seconds")
    if _eh_supabase(db):
        db.client.patch(TABELA, {"endpoint": f"eq.{endpoint}"}, {"ultimo_envio": agora, "ultimo_status": status})
        return
    with db.connect() as conn:
        conn.execute(f"UPDATE {TABELA} SET ultimo_envio = ?, ultimo_status = ? WHERE endpoint = ?", (agora, status, endpoint))


def enviar_para_todos(db: Any, payload: Dict[str, Any], enviar: EnviarFn = _enviar_urllib) -> Dict[str, int]:
    """Envia para todos os aparelhos. Inscrições expiradas (404/410) são removidas."""
    if not push_configurado():
        return {"enviadas": 0, "falhas": 0, "removidas": 0, "aparelhos": 0}
    inscricoes = listar_inscricoes(db)
    tot = {"enviadas": 0, "falhas": 0, "removidas": 0, "aparelhos": len(inscricoes)}
    for insc in inscricoes:
        endpoint = str(insc.get("endpoint") or "")
        try:
            status = enviar_para_inscricao(insc, payload, enviar=enviar)
        except Exception as exc:  # noqa: BLE001 - um aparelho com problema não derruba os outros
            logger.warning("Falha ao enviar push: %s", mask_secrets(str(exc)))
            tot["falhas"] += 1
            continue
        if status in (404, 410):
            remover_inscricao(db, endpoint)
            tot["removidas"] += 1
        elif 200 <= status < 300:
            tot["enviadas"] += 1
        else:
            logger.warning("Serviço de push respondeu HTTP %s", status)
            tot["falhas"] += 1
        try:
            if status not in (404, 410):
                _registrar_status(db, endpoint, status)
        except Exception:  # noqa: BLE001 - registro é só informativo
            pass
    return tot


# ---------------------------------------------------------------------------
# Composição das notificações (uma por rodada)
# ---------------------------------------------------------------------------
def _brl(valor: Any) -> str:
    try:
        v = float(valor or 0)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _cortar(texto: Any, n: int) -> str:
    t = " ".join(str(texto or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def montar_notificacao(tarefa: str, resultado: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Payload da notificação para o resultado de uma tarefa, ou None se não há nada novo."""
    if not isinstance(resultado, dict) or resultado.get("erro"):
        return None
    if tarefa == "varredura":
        n = int(resultado.get("pautas_novas") or 0)
        if n <= 0:
            return None
        titulos = [_cortar(t, 90) for t in (resultado.get("pautas_novas_titulos") or []) if t][:3]
        titulo = "1 pauta nova no Radar" if n == 1 else f"{n} pautas novas no Radar"
        corpo = "\n".join(f"• {t}" for t in titulos) or "Abra o painel para ver."
        return {"title": titulo, "body": corpo, "url": "/#pautas", "tag": "radar-pautas"}
    if tarefa == "pncp":
        n = int(resultado.get("inseridos") or 0)
        # Carga histórica (modo "carga") não avisa: só contratos que chegaram no incremental.
        if n <= 0 or resultado.get("modo") != "incremental":
            return None
        linhas = []
        for c in (resultado.get("novos_amostra") or [])[:3]:
            valor = _brl(c.get("valor_global"))
            nome = _cortar(c.get("fornecedor_nome") or c.get("objeto") or "Contrato", 60)
            linhas.append(f"• {nome}" + (f" — {valor}" if valor else ""))
        titulo = "1 contrato novo no PNCP" if n == 1 else f"{n} contratos novos no PNCP"
        return {"title": titulo, "body": "\n".join(linhas) or "Abra o painel para ver.", "url": "/#pncp", "tag": "radar-pncp"}
    return None


def notificar_resultado(db_factory: Callable[[], Any], tarefa: str, resultado: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Monta e envia a notificação de uma rodada. Nunca lança (push não pode quebrar a coleta)."""
    try:
        payload = montar_notificacao(tarefa, resultado)
        if payload is None or not push_configurado():
            return None
        tot = enviar_para_todos(db_factory(), payload)
        logger.info("Push '%s': %s", tarefa, tot)
        return tot
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha nas notificações push (%s): %s", tarefa, mask_secrets(str(exc)))
        return None


def notificar_em_segundo_plano(db_factory: Callable[[], Any], tarefa: str, resultado: Dict[str, Any]) -> None:
    if montar_notificacao(tarefa, resultado) is None:
        return
    threading.Thread(
        target=notificar_resultado, args=(db_factory, tarefa, resultado), name="radar-push", daemon=True
    ).start()


if __name__ == "__main__":  # pragma: no cover
    print(gerar_chave_privada_vapid())
