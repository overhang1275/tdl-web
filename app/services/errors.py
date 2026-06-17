from __future__ import annotations


def friendly_error(message: str | None) -> dict[str, str] | None:
    if not message:
        return None
    text = str(message).strip()
    lower = text.lower()
    if any(term in lower for term in ("redis", "connection refused", "error 61", "could not connect")):
        return {
            "title": "Redis apagado o inaccesible",
            "detail": "La cola de jobs no está disponible. Inicia Redis y el worker antes de crear o procesar descargas.",
            "technical": text,
        }
    if any(term in lower for term in ("unauthorized", "auth", "login", "not logged", "session", "phone code")):
        return {
            "title": "Sesión de Telegram vencida",
            "detail": "tdl no tiene una sesión activa. Vuelve a iniciar sesión desde Setup y reintenta el job.",
            "technical": text,
        }
    if any(term in lower for term in ("chat not found", "channel invalid", "peer", "forbidden", "private", "not a member")):
        return {
            "title": "Chat no accesible",
            "detail": "No se pudo leer ese chat. Revisa que la cuenta tenga acceso, que el ID sea correcto y que el chat siga disponible.",
            "technical": text,
        }
    if any(term in lower for term in ("json", "decode", "invalid character", "unexpected end", "corrupt")):
        return {
            "title": "Export corrupto o inválido",
            "detail": "El export.json no se pudo leer correctamente. Actualiza el export del chat y vuelve a ejecutar el job.",
            "technical": text,
        }
    if any(term in lower for term in ("no messages", "filtered messages: 0", "empty", "no media")):
        return {
            "title": "No hay mensajes con esos filtros",
            "detail": "El filtro no encontró contenido descargable. Prueba con otro tipo de medio, fecha, texto o hashtag.",
            "technical": text,
        }
    return {
        "title": "Error de descarga",
        "detail": "El job falló. Revisa el detalle técnico y los logs para decidir si conviene reintentar.",
        "technical": text,
    }
