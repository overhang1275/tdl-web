# Resumable Downloads Implementation Plan

**Goal:** Continuar descargas interrumpidas sin perder archivos verificados.
**Architecture:** Estado por archivo en SQL, fragmentos de exportación atómicos
y una invocación RQ por fragmento/lote. El mismo job conserva selección y progreso.
**Tech Stack:** Python, SQLAlchemy, SQLite, RQ, tdl 0.20.3.
**Spec:** ../specs/2026-10-05-resumable-downloads-design.md

## Global Constraints
- Sin nuevas dependencias; conservar configuración e historial actuales.
- Aplicación autorizada por el usuario en este chat; ejecución en este checkout.

## Review Focus
- No registrar temporales ni archivos de otros mensajes.
- Detectar archivos borrados antes de omitirlos.
- No duplicar trabajo al pulsar reintentar varias veces.
- Conservar progreso frente a muerte del worker y fallo al encolar.
- No publicar exportaciones parciales como caché completa.

## Tasks
- [x] Escribir pruebas de integración para el registro, interrupciones y reintento.
- [x] Ejecutarlas y confirmar fallos por comportamiento ausente.
- [x] Añadir modelos/migraciones y registro verificable en services/transfers.py.
- [x] Cambiar worker a fragmentos y lotes con continuación RQ y reintentos.
- [x] Añadir timeout de inactividad a TdlService y exportación por rango.
- [x] Actualizar botón, progreso, reconciliación y documentación.
- [x] Ejecutar suite completa, revisar diff y corregir fallos encontrados.

## Execution record
- Baseline: 33 pruebas pasan; warnings existentes de Starlette/datetime.utcnow.
- Ruling: se trabaja en el checkout actual conforme a «aplica los cambios».
- Evidencia tdl 0.20.3: app/dl/progress.go renombra el temporal solo tras éxito;
  app/chat/export.go admite rangos inclusivos de IDs y publica JSON al cerrar.
- Revisión independiente: corregidas carreras de cancelación/reintento y
  reconciliación de la cola. Un token anterior no puede finalizar otro intento.
- Regresiones adicionales: adopción de archivos legacy, temporales, archivos
  truncados/borrados, colas ausentes, fallos de encolado, exportación por rangos,
  migración repetible y botón visible en el partial de progreso.
- Verificación final: suite completa de pytest; no se usó Telegram real.
