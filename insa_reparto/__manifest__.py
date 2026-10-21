# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
{
    "name": "Reparto",
    # 18.0.1.2.0 — Sección 1 de los cambios de la reunión del 08/06:
    # control de playa con tres cantidades (C-05), devolución de producto
    # no conforme (C-06), conciliación de fallados (C-07), aprobación del
    # controlador con usuario y contraseña (C-08) y su fallback web (C-09).
    # 18.0.1.3.0 — Sección 2: listado único de visitas con marca de extra
    # (C-03), cobro sin venta por administración (C-04), columnas y
    # semáforo de préstamos/devoluciones (C-13/C-14), estado "Repaso"
    # (C-26) y renombre "Sin gestiones" (C-12).
    # 18.0.1.4.0 — Sección 3: día de la semana en minúscula + nombre
    # amigable (C-01), reordenamiento de pestañas (C-02), timestamps de
    # control y duración de jornada (C-10), relleno de fondo en
    # diferencias (C-15), columnas de pantallazo rápido + ratio préstamos/
    # ventas (C-16), pestaña Indicadores (C-17).
    # 18.0.1.5.0 — Sección 4: pantalla "Orden de clientes" (C-18) —
    # insa.ruta.plantilla, cierra el vacío V-02 de la spec v1.
    # 18.0.1.6.0 — Sección 5: FUE/FUC recalculado desde el dato real (fix
    # del hallazgo H-2, el cómputo viejo leía stock.picking, que el
    # reparto nunca genera) + umbral parametrizable (C-22), oportunidad
    # de recompra en CRM (C-23), datos base de planificación por móvil
    # (C-11, estructura a la espera del formulario de relevamiento V-14).
    # 18.0.1.7.0 — Paso 4: campos frecuencia y días de entrega en res.partner,
    # y endpoint REST stock-en-ruta para balance en vivo de camión.
    # 18.0.1.8.0 — Reunión de avances del 14/09: estados finales de la
    # hoja (R-01, sin borrador), el cierre confirma todo en bloque y genera
    # los pedidos de venta sin facturar (R-02), grupo "Administrativa de
    # cierre" (R-03), pestaña Transacciones con declaración de efectivo por
    # denominación (R-04), control de salida solo con llenos (R-05),
    # corrección desde la web con auditoría (R-06), diario de pérdidas con
    # motivo (R-07), hoja de ruta solo con clientes activos (R-08).
    # 18.0.1.9.0 — Revisión UX del 29/09: repartidor único en la hoja
    # (4.1), semáforo y "ver diferencias" en Liquidación (4.8), pedido
    # habitual por cliente con productos reales (4.3), completar pedidos
    # en bloque (4.2), control de salida precargado desde el pedido
    # habitual (4.4), saldo de envases con "a favor" y pantalla de
    # deudores (4.6), carga automática de hoja nueva con repartidor/
    # vehículo habituales (4.5), doble control de efectivo con
    # verificación independiente del controlador de playa (4.7).
    # 18.0.1.10.0 — Revisión UX del 02-05/10: "Control de playa" unificado
    # con selector Salida/Entrada (saca el botón nativo "Guardar y crear
    # nuevo" que chocaba con unique(hoja_ruta_id, tipo)), fusión de
    # Pedidos + Visitas en una fila por cliente (insa.pedido.visita),
    # Liquidación reubicada después de Pedidos, Planificación como
    # subsección de Indicadores, salida en dos tablas separadas (pedido
    # habitual / extra no atribuido a un cliente), entrada precargada con
    # los productos que salieron + columna de referencia "Salió",
    # statusbar nativo de OCA (stage_id, sin traducir) oculto en form y
    # lista.
    # 18.0.1.10.1 — fix: faltaba el context default_origen="habitual" en
    # la tabla "Según pedido habitual" del control de salida — una línea
    # agregada a mano ahí (sin ese default) caía en origen="extra" (el
    # default del campo) y, al guardar y recargar, aparecía abajo en
    # "Extra" en vez de arriba.
    # 18.0.1.10.2 — fix: el endpoint /insa/api/control-playa (lo usa la
    # app para guardar el control de playa) borraba y recreaba TODAS las
    # líneas en cada guardado, sin origen — cualquier guardado desde la
    # app tiraba todo a "extra" aunque el producto fuera del pedido
    # habitual precargado. Ahora preserva origen/a_cargar_pedidos de la
    # línea anterior por producto antes de recrear.
    # 18.0.1.11.0 — revisión 06/10: se retira el split habitual/extra del
    # control de salida (no era viable junto con la app) — vuelve a ser
    # una sola tabla sin título. En su lugar, "Llenos" no puede quedar
    # por debajo de "A cargar (pedidos)" (_check_salida_cubre_pedidos):
    # se puede cargar de más, nunca de menos de lo que piden los
    # clientes del día.
    # 18.0.1.12.0 — revisión 06/10: gastos de reparto (combustible, peaje,
    # comida, estacionamiento, mantenimiento, otro) — la pantalla "Elegir
    # tipo" de la app no tenía backend; se agrega insa.gasto.reparto, el
    # endpoint POST /insa/api/gasto, y el cuadro en Liquidación (se
    # descuentan del efectivo esperado, con detalle y comprobante para
    # conciliar).
    # 18.0.1.13.0 — revisión 07/10: abonos, migrado de Water Service
    # ("Clientes → Abonos"). insa.abono.tipo (catálogo de planes) e
    # insa.abono (asignado a un cliente, con historial al reemplazarlo),
    # con excedente por unidad vendible a demanda ("Vender excedente").
    # Mientras está activo, reemplaza al pedido habitual (4.3) para los
    # productos que cubre, con la cantidad por visita calculada desde la
    # 18.0.1.13.1 — fix: calcular fecha local (America/Argentina/Buenos_Aires)
    # en la API REST en vez de date.today() en UTC, y permitir autocreación
    # de hoja para hoy y fechas futuras.
    "version": "18.0.1.13.1",
    "license": "AGPL-3",
    "author": "Olpa Group",
    "application": True,
    "summary": "Control de playa, movimientos por cliente y liquidación diaria "
    "de reparto sobre la base OCA field-service.",
    "depends": [
        "fieldservice_route",
        "fieldservice_recurring",
        "fieldservice_stock",
        "stock",
        "sale",
        "fleet",
        "hr",
        "mail",
        # Paso 1.12: se formaliza como dependencia declarada — el
        # controlador ya usaba account.payment desde antes (_ultima_cobranza),
        # sin declararlo en el manifest.
        "account",
        # feature-formulario-alta: el formulario público de alta usa
        # website.layout y rutas website=True.
        "website",
        # el formulario de alta guarda insa_tax_condition y, a partir de
        # ahora, lo mapea automáticamente a la condición AFIP real
        # (l10n_ar_afip_responsibility_type_id) para que quede consistente
        # con lo que carga la migración de Water Service.
        "l10n_ar",
        # C-23: FUE vencido crea una oportunidad de recompra en CRM
        # (crm.lead, crm.tag).
        "crm",
    ],
    "data": [
        "security/insa_reparto_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/insa_reparto_perdida_motivo_data.xml",
        "views/fsm_route_views.xml",
        "views/fsm_location_views.xml",
        "views/insa_repartidor_credencial_views.xml",
        "views/insa_control_playa_views.xml",
        "views/insa_saldo_envase_views.xml",
        "views/insa_liquidacion_views.xml",
        "views/insa_ajuste_inventario_views.xml",
        "views/insa_ruta_plantilla_views.xml",
        "views/insa_abono_views.xml",
        "views/menu_reparto.xml",
        "views/fsm_route_dayroute_views.xml",
        "views/insa_movimiento_cliente_views.xml",
        "views/product_template_views.xml",
        "views/res_partner_views.xml",
        "views/hr_employee_views.xml",
        "views/insa_city_views.xml",
        "views/insa_street_views.xml",
        "views/insa_registration_templates.xml",
        "views/insa_perdida_views.xml",
    ],
    "assets": {
        # C-15: relleno de fondo en las filas con diferencia, en vez de
        # color de letra. Scopeado por clase a las listas de liquidación,
        # no toca decoration-warning en el resto de Odoo.
        "web.assets_backend": [
            "insa_reparto/static/src/css/insa_liquidacion.css",
        ],
    },
    "installable": True,
    "post_init_hook": "_renombrar_secuencia_hoja_ruta",
}
