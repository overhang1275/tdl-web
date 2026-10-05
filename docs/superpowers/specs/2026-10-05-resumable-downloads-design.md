# Descargas reanudables

Diseño aprobado en el chat: registro verificable por archivo, lotes independientes,
reintentos limitados y botón para continuar desde lo pendiente en el mismo job.

- Usar SQLite/SQLAlchemy y RQ existentes, sin nuevas dependencias.
- Registrar selección y estados por archivo. Los archivos `.tmp` de tdl no son
  completados; verificar el renombrado final y tamaño cuando se conoce.
- Aislar la salida nueva por job y mensaje para asociar archivos sin interpretar
  nombres de canales. Reutilizar descargas verificadas del mismo destino.
- Procesar hasta 100 mensajes por invocación RQ. Tras errores, aislar reintentos
  por mensaje, con tres intentos y esperas crecientes. Continuar otros archivos.
- Mantener límites máximos por lote y detectar inactividad durante descarga.
- Exportar por rangos de 5 000 IDs, guardando fragmentos y cursor; publicar
  export.json atómicamente solo cuando esté completo. Capturar un ID superior
  estable para no perseguir mensajes nuevos durante el trabajo.
- Reintentar el mismo job conserva filtros, export y completados; una nueva
  búsqueda se solicita con «Nuevo parecido». Cancelar detiene la continuación.
- Recuperar trabajos huérfanos como fallidos reanudables al reconciliar RQ.
- El JSON histórico de IDs se conserva, pero no se acepta como evidencia de
  un archivo existente y completo.

Verificación: interrupciones parciales, temporales, archivo faltante, cero
medios, lotes sucesivos, errores aislados, cancelación, reintento duplicado,
exportación fragmentada y timeouts con/sin progreso. No hace falta una sesión
real de Telegram para probar los límites con procesos controlados.
