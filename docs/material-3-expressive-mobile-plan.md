# Plan UI/UX Material 3 Expressive Mobile

Objetivo: modernizar la app con una capa visual estilo Material Design 3 Expressive, optimizada para movil, sin cambiar logica de negocio, endpoints ni pasos clave.

## Principios

- Mantener la estructura funcional actual: Dashboard, Chats, Jobs, Downloads, Setup, Notificaciones.
- Priorizar movil: navegacion con pulgar, controles de minimo 48px, jerarquia clara y acciones primarias visibles.
- Usar Material 3 / Material You: color vibrante, superficies claras, chips, FAB, cards, dialogs y barras de progreso.
- Agregar motion sutil: transiciones cortas, estados pressed/hover, progreso animado y loading claro.
- No crear dependencias nuevas salvo que sea estrictamente necesario.

## Fase 1: Base Visual

- Definir tokens CSS: colores, superficies, radius variados, sombras suaves, spacing y tamanos tactiles.
- Ajustar tipografia: titulos mas expresivos, texto de tabla/lista mas legible, labels compactos.
- Normalizar botones, pills, notices, panels y forms con lenguaje Material 3.
- Agregar motion base: transiciones de botones, cards, progress bars y loading states.

Criterio de salida:
- La app se ve consistente sin cambiar layouts importantes.
- Contraste y tamanos tactiles son aceptables en movil.

## Fase 2: Navegacion Movil

- Agregar bottom navigation fija en pantallas pequenas.
- Mantener navegacion actual en desktop.
- Agregar FAB contextual:
  - Dashboard: Nueva descarga.
  - Chats: Actualizar cache.
  - Jobs: Crear job.
- Evitar que FAB tape contenido con padding inferior responsivo.

Criterio de salida:
- En movil se puede navegar con una mano.
- Las acciones principales quedan visibles sin buscar en menus.

## Fase 3: Dashboard

- Convertir metricas en cards Material compactas.
- Crear un header de estado general moderado: listo, advertencia o error.
- Mostrar ultimos errores como lista escaneable con chips de severidad.
- Mantener links actuales a jobs y acciones existentes.

Criterio de salida:
- El estado del sistema se entiende en menos de 5 segundos.
- No se elimina informacion actual.

## Fase 4: Chats

- En movil reemplazar tabla por lista/cards ligeras.
- Usar search bar grande.
- Usar filter chips para tipo: Todos, Canales, Grupos, Privados.
- Mantener paginacion y selector de cantidad.
- Mostrar nombre, ID y tipo con jerarquia clara.
- Mantener accion Crear job por chat.

Criterio de salida:
- Buscar, filtrar y crear job desde movil es comodo.
- El orden del JSON sigue disponible.

## Fase 5: Jobs

- En movil presentar jobs como cards.
- Mostrar ID, chat, estado, stage, mensajes, archivos y modo.
- Mantener acciones Abrir, Reintentar, Cancelar y Eliminar Job.
- Para Eliminar Job, mantener confirmacion irreversible.
- Mostrar estados con pills Material y colores semanticos.

Criterio de salida:
- La lista de jobs deja de sentirse como tabla apretada en movil.
- Las acciones destructivas siguen siendo claras y confirmadas.

## Fase 6: Detalle de Job

- Dar protagonismo al progreso:
  - porcentaje.
  - encontrados / descargados.
  - tamano descargado.
  - velocidad.
  - ETA.
- Mantener logs visibles con estilo terminal limpio.
- Durante borrado con `wipe`, dejar logs en vivo como fuente principal de progreso.
- Mantener polling actual.

Criterio de salida:
- El usuario entiende que un job esta avanzando, fallando, cancelado o eliminandose.
- No se agrega flujo nuevo.

## Fase 7: Downloads

- Separar mejor galeria/lista, sin mezclar patrones visuales.
- En movil usar toggle Galeria / Lista si aplica.
- Mejorar previews de video/audio/imagen/PDF.
- Mantener filtros por tipo, busqueda, orden y paginacion.

Criterio de salida:
- Explorar archivos se siente consistente.
- Videos y previews no rompen el layout movil.

## Fase 8: Setup

- Convertir estado del sistema en checklist Material:
  - sesion activa.
  - tdl instalado.
  - Redis conectado.
  - worker conectado.
  - ultimo error de login.
- Destacar accion Iniciar/Reiniciar Redis y worker.
- Mantener login por codigo y consola actual.

Criterio de salida:
- Setup comunica salud del sistema sin leer logs primero.
- Acciones de reparacion quedan claras.

## Fase 9: Notificaciones

- Homologar visualmente con Chats y Jobs.
- Usar lista con status chips, timestamps y accion Abrir.
- Mantener toasts existentes con estilo Material.

Criterio de salida:
- Notificaciones dejan de sentirse como pantalla separada.
- Feedback de jobs completados/fallidos sigue funcionando.

## Fase 10: QA Movil

- Revisar anchos pequenos: 320px, 375px, 430px.
- Verificar que no haya textos cortados ni botones menores a 48px.
- Revisar contraste de estados.
- Revisar que bottom nav/FAB no tapen formularios, tablas o logs.
- Probar flujos:
  - login/setup.
  - actualizar cache de chats.
  - crear job.
  - ver progreso.
  - ver downloads.
  - eliminar job con `wipe`.

Criterio de salida:
- UI estable en movil y desktop.
- Tests existentes siguen pasando.

## No Objetivos

- No cambiar modelos de datos.
- No cambiar endpoints.
- No agregar una libreria UI pesada.
- No rehacer la app como SPA.
- No cambiar comportamiento de `tdl`, Redis, RQ o `wipe`.
