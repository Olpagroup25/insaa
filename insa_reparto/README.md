# Reparto (insa_reparto)

Módulo Odoo 18 para INSA — Agua y Soda (Córdoba, Argentina). Implementa
el flujo **D1: Hoja de Ruta → Reparto → Liquidación** sobre la base de
[OCA/field-service](https://github.com/OCA/field-service) (rama 18.0),
más una API REST para la futura app de reparto (D3) y un formulario
público de alta de contactos.

Licencia: AGPL-3.0. Ver [`docs/D1_Hoja_de_Ruta_Flujo_Punta_a_Punta.md`](docs/D1_Hoja_de_Ruta_Flujo_Punta_a_Punta.md)
para la especificación funcional completa (reglas de negocio, glosario,
casos de test) de la que sale todo lo implementado acá.

Estado: rama `feature-formulario-alta`, working tree limpio. Último commit:
`8209972` (Paso 2.7).

## Alcance

Cubre el ciclo diario de un reparto: control de playa de salida →
visitas (venta / cobro / préstamo / devolución / ausente / sin gestión)
→ control de playa de entrada → liquidación (efectivo, artículos,
envases) → consolidación → cierre (normal o forzado con motivo). Sobre
esa base se agregó una API REST para que la app de reparto (D3) opere
sin usuarios de Odoo, y un formulario web público de alta de contactos.

**No** incluye: app móvil en sí (D3, solo su backend), dispensers (D2),
GPS (D4), contabilidad completa ni conciliación bancaria. Esos quedan
documentados como TODO donde el código los toca.

## Modelos principales

| Modelo | Qué es |
|---|---|
| `fsm.route` (extendido) | La ruta/móvil persistente — trae `numero_reparto` ("Reparto 01"..."17") |
| `fsm.route.dayroute` (extendido) | La hoja de ruta del día — estados `en_reparto → consolidando → cerrada` (sin borrador desde la reunión del 14/09) |
| `insa.control.playa` / `.linea` | Conteo físico de mercadería (salida/entrada) |
| `insa.movimiento.cliente` / `.linea.articulo` / `.linea.envase` | Una visita: venta, cobro, movimiento de envase |
| `insa.pedido.extra` / `.linea` | Pedido inyectado por administración a una ruta en curso |
| `insa.liquidacion.articulo` / `.envase` | Cuadros computados de liquidación (teórico vs. registrado) |
| `insa.saldo.envase` | Saldo de envases por cliente (deuda vs. consumo habitual) |
| `insa.ajuste.inventario` | Auditoría de diferencias no resueltas al cerrar forzado |
| `insa.repartidor.credencial` | Credencial de la futura app de reparto (D3) — 1 por ruta, no por usuario Odoo |
| `insa.city` / `insa.street` | Nomenclador de ciudad/barrio y calle usado por el alta rápida (formulario público y app) |
| `res.partner` (extendido) | Dirección estructurada (`insa_city_id`/`insa_street_id`/...), condición fiscal, campos FUE/FUC |

## API REST para la app de reparto (D3)

Controlador [`controllers/insa_reparto_api.py`](controllers/insa_reparto_api.py).
Autenticación propia por token Bearer (`insa.repartidor.credencial`, ver
"Decisiones de arquitectura" más abajo), no usuarios de Odoo. Todas las
rutas son `auth="public"` porque la autenticación real la hace el
controlador a mano contra la credencial.

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/insa/api/login` | Login con credencial de ruta, devuelve el token |
| GET | `/insa/api/whoami` | Datos de la ruta/credencial del token, flags de feature |
| GET | `/insa/api/catalogo` | Catálogo de productos elegibles para control de playa |
| GET | `/insa/api/clientes/buscar` | Buscador de clientes por nombre/dirección (`?q=`), incluye clientes fuera de la hoja del día (Paso 2.6) |
| GET | `/insa/api/ciudades` | Ciudades/barrios para el alta rápida desde la app (Paso 2.7) |
| GET | `/insa/api/calles` | Calles de una ciudad (`?city_id=`), filtrado en servidor |
| POST | `/insa/api/clientes` | Alta rápida de cliente desde la app — a diferencia del formulario web, marca `customer_rank=1` (Paso 2.7) |
| GET | `/insa/api/hoja-de-ruta` | Hoja de ruta del día de la credencial: clientes, pedidos extra, deuda de envases |
| POST | `/insa/api/visita` | Crea/actualiza el movimiento de un cliente (venta, cobro, préstamo, devolución, etc.) |
| GET / POST | `/insa/api/control-playa` | Lee / registra control de playa de salida y entrada |
| POST | `/insa/api/control-playa/aprobar` | Aprobación del control de playa por un segundo factor (controlador) — 🔴 supuesto sin confirmar, ver abajo |
| GET | `/insa/api/liquidacion` | Liquidación de la hoja de ruta del día: efectivo, artículos, envases |

## Formulario público de alta de contactos

Controlador [`controllers/insa_registration_controller.py`](controllers/insa_registration_controller.py)
+ plantilla [`views/insa_registration_templates.xml`](views/insa_registration_templates.xml),
publicado en `/formulario-alta-insa` (requiere el módulo `website`).

- Sin autenticación (visitante anónimo del sitio), distinto del token de
  la API de reparto.
- Crea el `res.partner` vía `sudo()` desde el controlador — es el único
  punto del módulo con privilegio de escritura pública sobre
  `res.partner`/`insa.city`/`insa.street`; no se le dieron permisos ACL
  públicos a esos modelos.
- **No** marca `customer_rank`: un alta por este formulario es un
  contacto nuevo, no un cliente de reparto confirmado. No aparece en el
  Buscador (Paso 2.6) ni puede recibir visitas hasta que INSA lo
  convierta en cliente por el circuito normal de Odoo — decisión
  explícita, no un olvido. (El alta rápida vía API, `POST
  /insa/api/clientes`, sí marca `customer_rank=1` porque la hace un
  repartidor sobre un cliente real.)
- Flujo: `GET /formulario-alta-insa` (form) → `POST
  /formulario-alta-insa/enviar` (valida campos obligatorios, si hay
  errores re-renderiza el form con los datos cargados) → redirect a
  `/formulario-alta-insa/gracias`.

## Cómo correr los tests

```bash
odoo-bin -d <db> -i insa_reparto --test-enable --stop-after-init --test-tags insa_reparto
```

Los 7 casos obligatorios (CT-01 a CT-07, tomados textualmente de la
reunión del 18/07/2026 con INSA) están en
[`tests/test_d1_casos.py`](tests/test_d1_casos.py). **Esta suite se
escribió sin una instancia de Odoo corriendo — no se ejecutó todavía.**
Correrla y corregir lo que haga falta antes de dar la entrega por
cerrada. No cubre los endpoints de la API REST ni el formulario de alta
(ambos posteriores a la redacción de la suite).

## Decisiones de arquitectura no obvias

- **La "hoja de ruta" no es un modelo nuevo**: es `fsm.route.dayroute`
  (OCA `fieldservice_route`) extendido, no reinventado.
- **El saldo de envases por cliente** vive en `stock.quant`, en una
  `stock.location` que se crea automáticamente por cliente
  (`res_partner.py`) — no es un campo/modelo paralelo. Para identificar
  qué contactos son clientes reales de reparto (creación de esa
  ubicación, cron de FUE, etc.) se usa `customer_rank > 0`, no
  `type == 'fsm_location'` (fix posterior, ver historial).
- **La visita** (`insa.movimiento.cliente`) es un modelo propio,
  paralelo a `fsm.order` de OCA, por decisión explícita (no reescribir
  lo ya construido). Queda documentado como divergencia de nombres
  respecto al doc D1.
- **RN-16 (tope de envases)**: el checkpoint del 06/08/2026 decidió
  mantener `tolerancia_envase` (%) en vez de pasar a "consumo habitual
  exacto" como dice la transcripción literal. `tests/test_d1_casos.py`
  fuerza tolerancia 0% en el caso CT-05 para poder reproducir el
  número exacto de la reunión.
- **La autenticación de la futura app de reparto (D3)** no usa
  usuarios de Odoo (ni internos ni portal): es un token propio con
  forma de JWT (HS256, firmado con `hmac`/`hashlib` de la stdlib, sin
  agregar PyJWT como dependencia), una credencial por ruta/móvil, no
  por persona, 0 costo de licencia. El `login` de esa credencial tiene
  forma de email (`reparto01@insareparto.local`, dominio placeholder,
  no una casilla real) en vez de `"Reparto 01"` para que autocompletado
  de teclado / gestores de contraseña del lado Android lo reconozcan
  como credencial de acceso. Ver `models/insa_repartidor_credencial.py`
  y `controllers/insa_reparto_api.py`.
- **FUE/FUC (Paso 1.12)**: `fecha_ultima_entrega`/`fecha_ultima_cobranza`
  en `res.partner` son campos computados best-effort desde
  `stock.picking`/`account.payment` (no hay una fuente de negocio
  explícita para "última entrega"/"última cobranza" todavía). FUE
  vencido = 30 días fijos sin entrega (supuesto 0.5, confirmado por el
  cliente); dispara `ir.cron` diario (`data/ir_cron.xml`) que crea una
  actividad de Odoo por cliente vencido — el canal real de notificación
  (push/email/WhatsApp) sigue sin confirmar. Umbral de FUC configurable.
- **Alta de contactos, dos caminos con reglas distintas**: el formulario
  web público (`/formulario-alta-insa`) no marca `customer_rank`; el
  alta rápida de la API (`POST /insa/api/clientes`, usada desde la app
  por el repartidor) sí lo marca en 1. Mismo modelo (`res.partner`,
  `insa.city`/`insa.street`), reglas de negocio distintas según quién
  da el alta — ver sección de arriba.
- **Ajuste de inventario en cierre forzado (R-6.4)**: solo corrige
  stock para **envases** (el único producto que este módulo obliga a
  trackear). Para artículos queda solo como registro de auditoría — el
  conteo de playa ya es la cantidad física real, lo que falta ahí es el
  registro de venta, no una corrección de inventario. Sin asiento
  contable contra cuenta de mermas (deferido, [PARA INSA]).

## Pendientes documentados como TODO / [PARA INSA]

- Pestaña "Indicadores": sacada del form web (confirmado por el cliente
  el 21/08/2026 que va en otro módulo), no rehacer acá.
- Listas de precio (matriz / distribuidor / `.insa`, RN-20): fuera de
  alcance de esta entrega, falta relevar los porcentajes reales de la
  membresía `.insa` (V-04).
- Catálogo completo de artículos y envases (V-03).
- Asiento contable de mermas/faltantes en cierre forzado (R-6.4).
- Cascada de bloqueo `readonly` en "Cerrada" a los modelos hijos
  (control de playa, movimientos, pedidos extra) — hoy solo protege la
  cabecera de la hoja de ruta.
- `POST /insa/api/control-playa/aprobar` (segundo factor por
  controlador, `res.users.authenticate`): supuesto 0.1, 🔴 riesgo alto,
  sin confirmar con el cliente.
- Cotejo automático de devolución de artículo vs. control de playa:
  ambigüedad #8 del doc D1, implementado pero sin confirmar con el
  cliente.
- `fecha_ultima_entrega`/`fecha_ultima_cobranza` (FUE/FUC): la
  definición de negocio real de esos dos campos no está confirmada, hoy
  son best-effort desde `stock.picking`/`account.payment`. Canal de
  notificación de FUE vencido (push/email/WhatsApp) tampoco.
- Endpoints de datos de la app móvil (D3) más allá de los ya listados
  arriba: la app en sí (D3) no está construida, solo su backend.
