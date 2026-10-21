# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessDenied, UserError
from odoo.tools.safe_eval import safe_eval

from .insa_cierre import TIPOS_PERDIDA


class FSMRouteDayroute(models.Model):
    """Esta es la "hoja de ruta" (insa.hoja.ruta) de la tarjeta técnica,
    implementada como extensión de fsm.route.dayroute (OCA
    fieldservice_route) — NO como modelo nuevo. La tarjeta original decía
    fsm.route.day por error; fsm.route.day es solo el selector de día de
    semana (Monday..Sunday), fsm.route.dayroute es la hoja de ruta
    concreta de un repartidor en una fecha. Ver spike del 24/07/2026."""

    _name = "fsm.route.dayroute"
    _inherit = ["fsm.route.dayroute", "mail.thread"]

    reparto_numero = fields.Integer(
        string="N° de reparto / móvil",
        related="route_id.numero_reparto",
        store=True,
        readonly=True,
        tracking=True,
        help="Se hereda de la ruta (fsm.route.numero_reparto); ya no se "
        "tipea a mano en cada hoja de ruta para evitar que quede "
        "inconsistente día a día.",
    )
    dia_semana = fields.Selection(
        selection=[
            ("0", "lunes"),
            ("1", "martes"),
            ("2", "miércoles"),
            ("3", "jueves"),
            ("4", "viernes"),
            ("5", "sábado"),
            ("6", "domingo"),
        ],
        string="Día de la semana",
        compute="_compute_dia_semana",
        store=True,
        help="Derivado de `date` (date.weekday(), Lunes=0). Dato oficial "
        "para reportes/liquidación — la app puede seguir mostrando su "
        "propio cálculo en DateSelectionScreen, pero este es el que "
        "queda persistido en el backend. C-01: etiqueta en minúscula, tal "
        "como lo dicen ellos ('jueves móvil uno').",
    )
    vehiculo_id = fields.Many2one(comodel_name="fleet.vehicle", string="Vehículo")
    repartidor_id = fields.Many2one(
        comodel_name="hr.employee",
        string="Repartidor",
        tracking=True,
        help="Empleado asignado a este reparto. Complementa a person_id "
        "(fsm.person, nativo de OCA) para vincular con la ficha de "
        "legajo/nómina del repartidor.",
    )

    # R-01 (reunión 14/09, reemplaza §4 de la v1): no hay "Borrador"
    # visible — la hoja nace en "En reparto" (que incluye control de
    # salida, visitas y control de entrada), pasa a "Consolidando" cuando
    # la administrativa de cierre declara el efectivo del ticket, y a
    # "Cerrado" al finalizar. "Procesando cierre" se omite: el cierre es
    # instantáneo y Santi aceptó explícitamente no mostrarlo.
    # La clave técnica sigue siendo 'cerrada' (la app la lee por la API);
    # lo que cambia es la etiqueta visible.
    # Lo que antes bloqueaba "Borrador" (nada de visitas sin control de
    # salida aprobado, R-09) ahora lo hace control_salida_aprobado.
    state = fields.Selection(
        selection=[
            ("en_reparto", "En reparto"),
            ("consolidando", "Consolidando"),
            ("cerrada", "Cerrado"),
        ],
        default="en_reparto",
        required=True,
        tracking=True,
        copy=False,
    )
    efectivo_caja_repartidor = fields.Float(
        string="Efectivo declarado",
        readonly=True,
        copy=False,
        help="R-04: total del ticket del cajero de depósito donde el "
        "repartidor dejó la plata, cargado billete por billete por la "
        "administrativa de cierre (Declarar efectivo).",
    )
    # 4.7 (revisión 29/09): dos listas separadas por rol — mismo patrón
    # que control_salida_ids/control_entrada_ids (domain en Python, ver
    # esa nota más abajo: un domain solo en el XML no filtra de forma
    # confiable una lista embebida).
    declaracion_efectivo_ids = fields.One2many(
        comodel_name="insa.declaracion.efectivo",
        inverse_name="hoja_ruta_id",
        domain=[("rol", "=", "declarante")],
        string="Detalle por denominación",
        readonly=True,
    )
    declaracion_efectivo_controlador_ids = fields.One2many(
        comodel_name="insa.declaracion.efectivo",
        inverse_name="hoja_ruta_id",
        domain=[("rol", "=", "controlador")],
        string="Detalle por denominación (controlador)",
        readonly=True,
    )
    efectivo_declarado_por_id = fields.Many2one(
        comodel_name="res.users", string="Declarado por", readonly=True, copy=False
    )
    efectivo_declarado_fecha = fields.Datetime(
        string="Fecha de declaración", readonly=True, copy=False
    )
    efectivo_contado_controlador = fields.Float(
        string="Contado por el controlador",
        readonly=True,
        copy=False,
        help="4.7: segundo control, independiente de la declaración de la "
        "administrativa — el controlador de playa cuenta el mismo "
        "efectivo por su cuenta.",
    )
    controlador_verifico_por_id = fields.Many2one(
        comodel_name="res.users", string="Verificado por", readonly=True, copy=False
    )
    controlador_verifico_fecha = fields.Datetime(
        string="Fecha de verificación", readonly=True, copy=False
    )
    diferencia_efectivo_controlador = fields.Float(
        string="Diferencia declarado vs. contado",
        compute="_compute_efectivo",
        store=True,
    )
    perdida_ids = fields.One2many(
        comodel_name="insa.reparto.perdida",
        inverse_name="hoja_ruta_id",
        string="Pérdidas",
        readonly=True,
    )
    # Revisión 06/10: gastos cargados desde la app (combustible, peaje,
    # comida, estacionamiento, mantenimiento, otro) — se descuentan del
    # efectivo esperado en _compute_efectivo, ver ahí.
    gasto_ids = fields.One2many(
        comodel_name="insa.gasto.reparto",
        inverse_name="hoja_ruta_id",
        string="Gastos",
    )
    total_gastos = fields.Float(
        string="Gastos del reparto", compute="_compute_efectivo", store=True
    )

    # -- C-10: timestamps de los controles y lectura operativa ------------
    # "Si mi reparto uno tiene diez horas de viaje y acá me aparece que
    # tardó quince, vamos a poder identificar anormalidades." Se leen del
    # fecha_hora de cada control de playa (ya existía, C-05/C-08 no lo
    # tocó) — no son un dato nuevo a cargar, son un pantallazo de lo que
    # ya está.
    hora_salida = fields.Datetime(
        string="Hora de salida", compute="_compute_horas_control", store=True
    )
    hora_entrada = fields.Datetime(
        string="Hora de entrada", compute="_compute_horas_control", store=True
    )
    duracion_jornada = fields.Float(
        string="Duración de la jornada (hs)",
        compute="_compute_horas_control",
        store=True,
        help="Entrada − salida, en horas. Alimenta el bloque de "
        "Planificación (C-11) para detectar desvíos.",
    )

    # -- C-11: Planificación ------------------------------------------
    # "Si mi reparto uno tiene diez horas de viaje y acá me aparece que
    # tardó quince, vamos a poder identificar anormalidades." work_time
    # (previsto) y start_location_id/end_location_id son campos NATIVOS
    # de OCA fieldservice_route (pestaña "Planning" ya existente) — no se
    # duplican acá, solo se completan por defecto desde route_id al crear
    # la hoja (ver create()) y se compara duracion_jornada (real, C-10)
    # contra work_time (previsto).
    desvio_jornada = fields.Float(
        string="Desvío (hs)",
        compute="_compute_desvio_jornada",
        help="duracion_jornada − work_time. Vacío hasta que work_time "
        "tenga un valor (route_id.duracion_prevista, a la espera del "
        "formulario de relevamiento — V-14).",
    )
    tiene_desvio = fields.Boolean(
        string="Tiene desvío", compute="_compute_desvio_jornada"
    )

    @api.model_create_multi
    def create(self, vals_list):
        # C-11: completa los datos base de planificación del móvil al
        # crear la hoja del día — no se re-tipean cada vez, se heredan de
        # fsm.route (ver ubicacion_inicio_id/ubicacion_fin_id/
        # duracion_prevista). Solo si la hoja no trae ya un valor propio.
        #
        # 4.5 (revisión 29/09): unifica el botón "Nuevo" de Hojas de Ruta
        # con lo que hasta ahora solo pasaba generando desde "Orden de
        # clientes" — repartidor/vehículo habituales (mismo criterio de
        # "solo si no viene cargado") y los clientes del día, si existe
        # una plantilla para esa ruta+día de la semana.
        for vals in vals_list:
            route_id = vals.get("route_id")
            if not route_id:
                continue
            route = self.env["fsm.route"].browse(route_id)
            if "start_location_id" not in vals and route.ubicacion_inicio_id:
                vals["start_location_id"] = route.ubicacion_inicio_id.id
            if "end_location_id" not in vals and route.ubicacion_fin_id:
                vals["end_location_id"] = route.ubicacion_fin_id.id
            if "work_time" not in vals and route.duracion_prevista:
                vals["work_time"] = route.duracion_prevista
            if "repartidor_id" not in vals and route.repartidor_habitual_id:
                vals["repartidor_id"] = route.repartidor_habitual_id.id
            if "vehiculo_id" not in vals and route.vehiculo_habitual_id:
                vals["vehiculo_id"] = route.vehiculo_habitual_id.id
        records = super().create(vals_list)
        for rec in records:
            if not rec.route_id or not rec.date:
                continue
            plantilla = self.env["insa.ruta.plantilla"].search(
                [
                    ("route_id", "=", rec.route_id.id),
                    ("dia_semana", "=", str(rec.date.weekday())),
                ],
                limit=1,
            )
            if plantilla:
                # _poblar_pedidos_en_hoja(rec), no action_generar_hoja_ruta
                # (que busca la hoja por ruta+fecha): fsm.route.dayroute no
                # tiene restricción de unicidad, así que esa búsqueda
                # puede encontrar OTRA hoja ya existente para la misma
                # ruta+fecha y cargar los pedidos ahí, dejando esta (rec,
                # la que se acaba de crear) vacía.
                plantilla._poblar_pedidos_en_hoja(rec)
            # 4.4/4.2 (revisión 02/10, reubicado 03/10): el control de
            # salida se crea solo, después de poblar los pedidos (arriba)
            # — así su create() encuentra order_ids ya cargado y puede
            # precargar desde el pedido habitual (_precargar_pedido_habitual).
            # Antes el repartidor tenía que entrar a otra pestaña y tocar
            # "Agregar una línea" antes de ver nada.
            if not rec.control_salida_ids:
                self.env["insa.control.playa"].create(
                    {"hoja_ruta_id": rec.id, "tipo": "salida"}
                )
        return records

    @api.depends("duracion_jornada", "work_time")
    def _compute_desvio_jornada(self):
        for rec in self:
            if rec.work_time and rec.duracion_jornada:
                rec.desvio_jornada = rec.duracion_jornada - rec.work_time
            else:
                rec.desvio_jornada = 0.0
            umbral = float(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("insa_reparto.umbral_desvio_horas", "1.0")
            )
            rec.tiene_desvio = bool(rec.work_time) and abs(rec.desvio_jornada) > umbral

    control_playa_ids = fields.One2many(
        comodel_name="insa.control.playa",
        inverse_name="hoja_ruta_id",
        string="Controles de playa",
    )
    # R-04: una pestaña por control. Campo propio con domain en Python por
    # la misma razón que movimiento_cobro_efectivo_ids (ver abajo).
    control_salida_ids = fields.One2many(
        comodel_name="insa.control.playa",
        inverse_name="hoja_ruta_id",
        domain=[("tipo", "=", "salida")],
        string="Control de salida",
    )
    # Revisión 02/10: Control de salida unificado adentro de Pedidos — se
    # necesita un Many2one al único control (unique(hoja_ruta_id, tipo))
    # para poder mostrar sus líneas directo en la vista, sin que Odoo
    # renderice la lista de "controles" (que ignora cualquier <form>
    # embebido en un O2M y usa la vista list registrada del modelo).
    control_salida_id = fields.Many2one(
        comodel_name="insa.control.playa",
        compute="_compute_control_salida_id",
        string="Control de salida",
    )
    control_salida_linea_ids = fields.One2many(
        related="control_salida_id.linea_ids", readonly=False
    )
    control_salida_state = fields.Selection(related="control_salida_id.state")
    control_salida_aprobado_por_id = fields.Many2one(
        related="control_salida_id.aprobado_por_id"
    )
    control_salida_aprobado_fecha = fields.Datetime(
        related="control_salida_id.aprobado_fecha"
    )
    control_salida_fecha_hora = fields.Datetime(related="control_salida_id.fecha_hora")
    control_salida_kilometros = fields.Float(
        related="control_salida_id.kilometros", readonly=False
    )

    # Revisión 03/10: selector puramente de pantalla (no se guarda) para
    # elegir qué conteo se muestra dentro del tab único "Control de
    # playa" — RN-04 ya garantiza que solo hay un control de cada tipo
    # por hoja, este campo solo decide cuál de los dos se ve.
    control_playa_vista = fields.Selection(
        selection=[("salida", "Salida"), ("entrada", "Entrada")],
        string="Ver",
        default="salida",
    )

    @api.depends("control_salida_ids")
    def _compute_control_salida_id(self):
        for rec in self:
            rec.control_salida_id = rec.control_salida_ids[:1]

    def action_aprobar_control_salida(self):
        self.ensure_one()
        return self.control_salida_id.action_abrir_wizard_aprobar()

    def action_volver_a_borrador_control_salida(self):
        self.ensure_one()
        return self.control_salida_id.action_volver_a_borrador()

    control_entrada_ids = fields.One2many(
        comodel_name="insa.control.playa",
        inverse_name="hoja_ruta_id",
        domain=[("tipo", "=", "entrada")],
        string="Control de entrada",
    )
    # Revisión 03/10: mismo patrón que control_salida_id — un Many2one al
    # único control (unique(hoja_ruta_id, tipo)) para mostrar sus líneas
    # directo en la vista sin el botón nativo "Guardar y crear nuevo" que
    # permitía intentar un segundo control de playa y chocar con esa
    # restricción (el error de validación que vio el cliente). A
    # diferencia de salida, entrada no se crea sola al crear la hoja — se
    # crea recién cuando el camión vuelve (action_crear_control_entrada).
    control_entrada_id = fields.Many2one(
        comodel_name="insa.control.playa",
        compute="_compute_control_entrada_id",
        string="Control de entrada",
    )
    control_entrada_linea_ids = fields.One2many(
        related="control_entrada_id.linea_ids", readonly=False
    )
    control_entrada_state = fields.Selection(related="control_entrada_id.state")
    control_entrada_aprobado_por_id = fields.Many2one(
        related="control_entrada_id.aprobado_por_id"
    )
    control_entrada_aprobado_fecha = fields.Datetime(
        related="control_entrada_id.aprobado_fecha"
    )
    control_entrada_fecha_hora = fields.Datetime(related="control_entrada_id.fecha_hora")
    control_entrada_kilometros = fields.Float(
        related="control_entrada_id.kilometros", readonly=False
    )

    @api.depends("control_entrada_ids")
    def _compute_control_entrada_id(self):
        for rec in self:
            rec.control_entrada_id = rec.control_entrada_ids[:1]

    def action_crear_control_entrada(self):
        self.ensure_one()
        if not self.control_entrada_id:
            self.env["insa.control.playa"].create(
                {"hoja_ruta_id": self.id, "tipo": "entrada"}
            )

    def action_aprobar_control_entrada(self):
        self.ensure_one()
        return self.control_entrada_id.action_abrir_wizard_aprobar()

    def action_volver_a_borrador_control_entrada(self):
        self.ensure_one()
        return self.control_entrada_id.action_volver_a_borrador()

    # Revisión 03/10: fusión de Pedidos + Visitas, ver insa_pedido_visita.py.
    pedido_visita_ids = fields.One2many(
        comodel_name="insa.pedido.visita",
        inverse_name="hoja_ruta_id",
        string="Pedidos y visitas",
    )
    movimiento_cliente_ids = fields.One2many(
        comodel_name="insa.movimiento.cliente",
        inverse_name="hoja_ruta_id",
        string="Movimientos por cliente",
    )
    # C-04/C-13: subconjuntos de movimiento_cliente_ids para las pestañas
    # de solo lectura. Hace falta un campo Python propio por cada uno, con
    # el domain declarado acá —no alcanza con poner domain="..." en el
    # <field> de la vista: el cliente web de Odoo 18 comparte el estado
    # del mismo nombre de campo o2m entre pestañas de un mismo formulario,
    # así que un domain puesto solo en el XML no siempre filtra lo que se
    # muestra (verificado: el ORM filtra bien, la lista embebida no).
    movimiento_cobro_efectivo_ids = fields.One2many(
        comodel_name="insa.movimiento.cliente",
        inverse_name="hoja_ruta_id",
        domain=[("cobro_efectivo", "!=", 0), ("medio_pago", "=", "efectivo")],
        string="Cobros en efectivo",
    )
    movimiento_envase_ids = fields.One2many(
        comodel_name="insa.movimiento.cliente",
        inverse_name="hoja_ruta_id",
        domain=[("tiene_mov_envase", "=", True)],
        string="Movimientos con envase",
    )
    pedido_extra_ids = fields.One2many(
        comodel_name="insa.pedido.extra",
        inverse_name="hoja_ruta_id",
        string="Pedidos extra",
    )
    liquidacion_articulo_ids = fields.One2many(
        comodel_name="insa.liquidacion.articulo",
        inverse_name="hoja_ruta_id",
        string="Liquidación de artículos",
    )
    liquidacion_envase_ids = fields.One2many(
        comodel_name="insa.liquidacion.envase",
        inverse_name="hoja_ruta_id",
        string="Liquidación de envases",
    )
    liquidacion_fallado_ids = fields.One2many(
        comodel_name="insa.liquidacion.fallado",
        inverse_name="hoja_ruta_id",
        string="Conciliación de fallados",
    )
    ajuste_inventario_ids = fields.One2many(
        comodel_name="insa.ajuste.inventario",
        inverse_name="hoja_ruta_id",
        string="Ajustes de inventario por cierre forzado",
    )
    # C-16: "si todos los días me está prestando envase y sus ventas no lo
    # justifican, quiere decir mucho dinero en la calle" — propuesta
    # nuestra derivada de ese dolor, no pedida textualmente; validar con
    # Nico antes de darla por definitiva.
    ratio_prestamos_ventas = fields.Float(
        string="Préstamos / Ventas",
        compute="_compute_ratio_prestamos_ventas",
        help="Unidades de envase prestadas hoy sobre unidades de artículo "
        "vendidas hoy. Un ratio alto sin ventas que lo expliquen es la "
        "señal de un repartidor que presta de más.",
    )
    # -- C-17: Indicadores del reparto -------------------------------------
    # "Nos dice qué cantidad de clientes tiene ese reparto, cuántos se
    # visitaron... visitados 82 de 100, sin gestión 47, ausente 30." Vuelve
    # a existir como pestaña propia — la vista tenía un cartel desde el
    # 21/08/2026 que decía que esta información se había sacado porque el
    # cliente había confirmado que iba en otro módulo. La reunión del
    # 08/06 revierte ese criterio explícitamente (C-17); queda documentado
    # acá para que no se vuelva a discutir en tres meses (hallazgo H-5).
    #
    # "Visitado" acá es 'tiene alguna gestión cargada' (venta, ausente,
    # sin gestión, cobro o envase) — el mismo criterio que ya usa la API
    # para "pendiente" en _serializar_cliente, no el campo movimiento.
    # visitado (que es 'hubo visita física', RN-18, y puede ser False a
    # propósito). Se cuenta solo sobre los clientes de la ruta fija del
    # día (order_ids) — las visitas extra (C-03) no forman parte de "qué
    # % de mi cartera de hoy compró algo".
    total_clientes = fields.Integer(compute="_compute_indicadores")
    visitados_count = fields.Integer(string="Visitados", compute="_compute_indicadores")
    visitados_pct = fields.Float(
        string="% visitados",
        compute="_compute_indicadores",
        help="Fracción 0-1 (widget percentage): 82% se guarda como 0.82.",
    )
    con_venta_count = fields.Integer(string="Con venta", compute="_compute_indicadores")
    ausentes_count = fields.Integer(string="Ausentes", compute="_compute_indicadores")
    sin_gestion_count = fields.Integer(string="Sin gestiones", compute="_compute_indicadores")
    con_cobro_count = fields.Integer(string="Con cobro", compute="_compute_indicadores")
    con_mov_envase_count = fields.Integer(
        string="Con movimiento de envases", compute="_compute_indicadores"
    )
    pendientes_count = fields.Integer(string="Pendientes", compute="_compute_indicadores")

    @api.depends(
        "order_ids.location_id.partner_id",
        "movimiento_cliente_ids.partner_id",
        "movimiento_cliente_ids.resultado",
        "movimiento_cliente_ids.tiene_venta",
        "movimiento_cliente_ids.tiene_cobro",
        "movimiento_cliente_ids.tiene_mov_envase",
    )
    def _compute_indicadores(self):
        for rec in self:
            partners_ruta = rec.order_ids.mapped("location_id.partner_id")
            movs = rec.movimiento_cliente_ids.filtered(
                lambda m: m.partner_id in partners_ruta
            )
            rec.total_clientes = len(partners_ruta)
            rec.visitados_count = len(movs)
            rec.visitados_pct = (len(movs) / len(partners_ruta)) if partners_ruta else 0.0
            rec.con_venta_count = len(movs.filtered("tiene_venta"))
            rec.ausentes_count = len(movs.filtered(lambda m: m.resultado == "ausente"))
            rec.sin_gestion_count = len(
                movs.filtered(lambda m: m.resultado == "sin_gestion")
            )
            rec.con_cobro_count = len(movs.filtered("tiene_cobro"))
            rec.con_mov_envase_count = len(movs.filtered("tiene_mov_envase"))
            rec.pendientes_count = len(partners_ruta) - len(movs)

    total_efectivo_registrado = fields.Float(
        string="Efectivo registrado",
        compute="_compute_efectivo",
        store=True,
    )
    diferencia_efectivo = fields.Float(
        string="Diferencia de efectivo",
        compute="_compute_efectivo",
        store=True,
    )
    tiene_diferencias = fields.Boolean(
        string="Tiene diferencias",
        compute="_compute_tiene_diferencias",
        store=True,
    )
    tiene_discordancia_fallados = fields.Boolean(
        string="Discordancia de fallados",
        compute="_compute_tiene_diferencias",
        store=True,
        help="C-07: el repartidor y el control de playa no coinciden en "
        "cuántos productos no conformes volvieron.",
    )

    close_forced = fields.Boolean(string="Cerrada con diferencias", readonly=True, copy=False)
    close_forced_reason = fields.Text(string="Motivo del cierre forzado", readonly=True, copy=False)

    # 4.8 (revisión 29/09): resumen superior de Liquidación — semáforo por
    # sección, para ver de un vistazo si hay algo para revisar sin entrar
    # a cada cuadro. 4.7: Efectivo se suma como cuarta sección.
    resumen_articulos = fields.Char(compute="_compute_resumen_liquidacion")
    resumen_envases = fields.Char(compute="_compute_resumen_liquidacion")
    resumen_fallados = fields.Char(compute="_compute_resumen_liquidacion")
    resumen_efectivo = fields.Char(compute="_compute_resumen_liquidacion")
    liquidacion_ok = fields.Boolean(
        string="Liquidación conciliada",
        compute="_compute_resumen_liquidacion",
        help="Artículos, envases, fallados y efectivo sin ninguna diferencia.",
    )
    # 4.8: "solo diferencias" a un clic. El atributo domain="" de una
    # vista NO filtra las líneas ya cargadas de un One2many embebido en
    # un formulario (se probó: ni recién abierto ni al togglear nada se
    # ocultan filas) — solo afecta qué se puede vincular al crear. La
    # forma que sí funciona en Odoo es abrir la pantalla aparte
    # (insa.liquidacion.*, ya con su propio buscador real) filtrada a
    # esta hoja y con "Con diferencias" prendido.
    def _action_ver_diferencias(self, modelo, action_xmlid):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "insa_reparto.%s" % action_xmlid
        )
        action["domain"] = [("hoja_ruta_id", "=", self.id)]
        # _for_xml_id devuelve el contexto tal cual está guardado (un
        # string sin evaluar), no un dict.
        contexto = safe_eval(action.get("context") or "{}")
        action["context"] = dict(contexto, search_default_con_diferencias=1)
        return action

    def action_ver_diferencias_articulos(self):
        return self._action_ver_diferencias(
            "insa.liquidacion.articulo", "action_insa_liquidacion_articulo"
        )

    def action_ver_diferencias_envases(self):
        return self._action_ver_diferencias(
            "insa.liquidacion.envase", "action_insa_liquidacion_envase"
        )

    def action_ver_diferencias_fallados(self):
        return self._action_ver_diferencias(
            "insa.liquidacion.fallado", "action_insa_liquidacion_fallado"
        )

    @api.depends(
        "liquidacion_articulo_ids.estado",
        "liquidacion_envase_ids.estado",
        "liquidacion_fallado_ids.estado",
        "diferencia_efectivo",
        "diferencia_efectivo_controlador",
        "controlador_verifico_por_id",
    )
    def _compute_resumen_liquidacion(self):
        for rec in self:
            def resumen(lineas):
                n = len(lineas.filtered(lambda l: l.estado == "revisar"))
                return _("✔ Conciliado") if n == 0 else _("⚠ %d por revisar") % n

            rec.resumen_articulos = resumen(rec.liquidacion_articulo_ids)
            rec.resumen_envases = resumen(rec.liquidacion_envase_ids)
            rec.resumen_fallados = resumen(rec.liquidacion_fallado_ids)
            # 4.7: "revisar" si falta el segundo control, o si cualquiera
            # de las dos diferencias (registrado↔declarado,
            # declarado↔contado) no da cero.
            efectivo_ok = (
                bool(rec.controlador_verifico_por_id)
                and rec.diferencia_efectivo == 0
                and rec.diferencia_efectivo_controlador == 0
            )
            rec.resumen_efectivo = _("✔ Conciliado") if efectivo_ok else _("⚠ Revisar")
            # No se pueden unir con | recordsets de modelos distintos
            # (insa.liquidacion.articulo/envase/fallado): se revisa cada
            # uno por separado.
            rec.liquidacion_ok = efectivo_ok and not any(
                lineas.filtered(lambda l: l.estado == "revisar")
                for lineas in (
                    rec.liquidacion_articulo_ids,
                    rec.liquidacion_envase_ids,
                    rec.liquidacion_fallado_ids,
                )
            )
    close_forced_by = fields.Many2one(
        comodel_name="res.users", string="Cerrada por", readonly=True, copy=False
    )
    close_forced_date = fields.Datetime(string="Fecha de cierre forzado", readonly=True, copy=False)

    def write(self, vals):
        # §5.7: "Cerrada" es readonly total, sin edición posterior. Se lee
        # rec.state ANTES de aplicar vals, así que el propio write que
        # pasa a 'cerrada' (hecho desde 'consolidando') no se autobloquea.
        # Nota: esto solo protege la cabecera; los modelos hijos (control
        # de playa, movimientos por cliente, pedidos extra) todavía no
        # heredan este bloqueo — queda como gap documentado, no se
        # generalizó por no ser parte explícita de RN-12/13/14 ni de CT-07.
        for rec in self:
            if rec.state == "cerrada":
                raise UserError(
                    _("La hoja de ruta %s está cerrada, no se puede modificar.")
                    % rec.name
                )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state == "cerrada":
                raise UserError(
                    _("La hoja de ruta %s está cerrada, no se puede eliminar.")
                    % rec.name
                )
        return super().unlink()

    @api.depends("date")
    def _compute_dia_semana(self):
        for rec in self:
            rec.dia_semana = str(rec.date.weekday()) if rec.date else False

    @api.depends("reparto_numero", "dia_semana", "date")
    def _compute_display_name(self):
        # C-01: "Reparto 1 — jueves 08/06" en todo lugar donde se referencia
        # la hoja de ruta por texto (breadcrumb, un Many2one apuntando
        # acá). El campo 'name' técnico (REP/2026/NNNN, la secuencia de
        # post_init_hook) no se toca: sigue siendo el identificador que se
        # ve en las listas y se usa para buscar/ordenar.
        dias = dict(self._fields["dia_semana"].selection)
        for rec in self:
            if rec.reparto_numero and rec.date:
                rec.display_name = _("Reparto %(numero)s — %(dia)s %(fecha)s") % {
                    "numero": rec.reparto_numero,
                    "dia": dias.get(rec.dia_semana, ""),
                    "fecha": rec.date.strftime("%d/%m"),
                }
            else:
                rec.display_name = rec.name

    @api.depends(
        "movimiento_cliente_ids.linea_articulo_ids.tipo",
        "movimiento_cliente_ids.linea_articulo_ids.cantidad",
        "movimiento_cliente_ids.linea_envase_ids.tipo",
        "movimiento_cliente_ids.linea_envase_ids.cantidad",
    )
    def _compute_ratio_prestamos_ventas(self):
        for rec in self:
            lineas_articulo = rec.movimiento_cliente_ids.linea_articulo_ids
            lineas_envase = rec.movimiento_cliente_ids.linea_envase_ids
            ventas = sum(
                lineas_articulo.filtered(lambda l: l.tipo == "venta").mapped("cantidad")
            )
            prestamos = sum(
                lineas_envase.filtered(lambda l: l.tipo == "prestamo").mapped("cantidad")
            )
            rec.ratio_prestamos_ventas = prestamos / ventas if ventas else 0.0

    @api.depends("control_playa_ids.tipo", "control_playa_ids.fecha_hora")
    def _compute_horas_control(self):
        for rec in self:
            salida = rec.control_playa_ids.filtered(lambda c: c.tipo == "salida")[:1]
            entrada = rec.control_playa_ids.filtered(lambda c: c.tipo == "entrada")[:1]
            rec.hora_salida = salida.fecha_hora or False
            rec.hora_entrada = entrada.fecha_hora or False
            if rec.hora_salida and rec.hora_entrada:
                rec.duracion_jornada = (
                    rec.hora_entrada - rec.hora_salida
                ).total_seconds() / 3600.0
            else:
                rec.duracion_jornada = 0.0

    @api.depends(
        "movimiento_cliente_ids.cobro_efectivo",
        "movimiento_cliente_ids.medio_pago",
        "efectivo_caja_repartidor",
        "efectivo_contado_controlador",
        "gasto_ids.importe",
    )
    def _compute_efectivo(self):
        # RN-10 / C-04: la liquidación de efectivo solo contempla cobros
        # con medio_pago == 'efectivo'. Un cobro en cheque o transferencia
        # (por ejemplo cargado por administración sobre un cliente que no
        # es del día) queda trazado en la visita pero no entra acá.
        for rec in self:
            cobros_efectivo = rec.movimiento_cliente_ids.filtered(
                lambda m: m.medio_pago == "efectivo"
            )
            rec.total_efectivo_registrado = sum(cobros_efectivo.mapped("cobro_efectivo"))
            rec.total_gastos = sum(rec.gasto_ids.mapped("importe"))
            # Revisión 06/10: si el repartidor pagó un gasto (combustible,
            # peaje, etc.) con la plata que cobró en la ruta, lo que trae
            # de vuelta para declarar es legítimamente menor al total
            # cobrado — sin restar el gasto, esa diferencia se marcaba
            # como un faltante que en realidad no lo es.
            rec.diferencia_efectivo = (
                rec.total_efectivo_registrado - rec.total_gastos
            ) - rec.efectivo_caja_repartidor
            rec.diferencia_efectivo_controlador = (
                rec.efectivo_caja_repartidor - rec.efectivo_contado_controlador
            )

    @api.depends(
        "liquidacion_articulo_ids.diferencia",
        "liquidacion_envase_ids.diferencia",
        "liquidacion_fallado_ids.discordancia",
        "diferencia_efectivo",
    )
    def _compute_tiene_diferencias(self):
        for rec in self:
            rec.tiene_discordancia_fallados = any(
                rec.liquidacion_fallado_ids.mapped(lambda l: l.discordancia != 0)
            )
            rec.tiene_diferencias = (
                any(rec.liquidacion_articulo_ids.mapped(lambda l: l.diferencia != 0))
                or any(rec.liquidacion_envase_ids.mapped(lambda l: l.diferencia != 0))
                or rec.tiene_discordancia_fallados
                or rec.diferencia_efectivo != 0
            )

    def action_abrir_credencial_ruta(self):
        """4.1: acceso rápido a la credencial de la app desde la hoja de
        ruta. La credencial (insa.repartidor.credencial) es de la Ruta
        (route_id), no del repartidor de este día — no cambia si cambia
        quién maneja, ver insa_repartidor_credencial.py."""
        self.ensure_one()
        credencial = self.env["insa.repartidor.credencial"].search(
            [("route_id", "=", self.route_id.id)], limit=1
        )
        if not credencial:
            raise UserError(
                _(
                    "La ruta %(ruta)s todavía no tiene una credencial de app "
                    "creada."
                )
                % {"ruta": self.route_id.display_name}
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Credencial de la ruta"),
            "res_model": "insa.repartidor.credencial",
            "res_id": credencial.id,
            "view_mode": "form",
            "target": "current",
        }

    num_pedidos_pendientes = fields.Integer(
        string="Pedidos pendientes",
        compute="_compute_num_pedidos_pendientes",
    )

    @api.depends("order_ids.stage_id")
    def _compute_num_pedidos_pendientes(self):
        for rec in self:
            rec.num_pedidos_pendientes = len(
                rec.order_ids.filtered(
                    lambda o: o.stage_id == self.env.ref("fieldservice.fsm_stage_new")
                )
            )

    def action_completar_todos_los_pendientes(self):
        """4.2 (revisión 29/09): marca de una todos los pedidos (fsm.order,
        "paradas") de esta hoja que siguen en etapa Nuevo como
        Completados — ignora los que ya están Completados o Cancelados.
        Reusa action_complete, el mismo método nativo de OCA que usa el
        botón por fila."""
        self.ensure_one()
        pendientes = self.order_ids.filtered(
            lambda o: o.stage_id == self.env.ref("fieldservice.fsm_stage_new")
        )
        if not pendientes:
            raise UserError(_("No hay pedidos pendientes para completar."))
        pendientes.action_complete()
        self._message_log(
            body=_("%d pedidos marcados como completados en bloque.") % len(pendientes)
        )

    def action_recalcular_liquidacion(self):
        for rec in self:
            if rec.state == "cerrada":
                raise UserError(
                    _("La hoja de ruta %s está cerrada: su liquidación ya no se recalcula.")
                    % rec.name
                )
            rec.liquidacion_articulo_ids.unlink()
            rec.liquidacion_envase_ids.unlink()
            rec.liquidacion_fallado_ids.unlink()
            rec._crear_liquidacion_articulos()
            rec._crear_liquidacion_envases()
            rec._crear_liquidacion_fallados()
        return True

    def _crear_liquidacion_articulos(self):
        salidas, entradas, registrado = {}, {}, {}
        for playa in self.control_playa_ids:
            destino = salidas if playa.tipo == "salida" else entradas
            for linea in playa.linea_ids:
                # Solo los LLENOS: un vacío o un fallado que volvió no
                # cancela un artículo entregado (C-05). El vacío se
                # concilia en el cuadro de envases y el fallado en el de
                # fallados.
                destino[linea.product_id] = destino.get(linea.product_id, 0.0) + linea.cantidad
        for mov in self.movimiento_cliente_ids:
            for linea in mov.linea_articulo_ids:
                # C-06: qué cuenta como "entregado y registrado".
                #   - venta: obvio, salió un lleno y se cobró.
                #   - devolución CON cambio directo: no es venta, pero
                #     salió igual un lleno del camión (se lo dejó al
                #     cliente en el acto), así que suma. Lo que vuelve es
                #     un fallado, y eso se concilia aparte (C-07).
                #   - devolución SIN cambio directo: el cliente devuelve y
                #     no se lleva nada; no salió ningún lleno, no toca
                #     este cuadro.
                # Ojo: hasta el 08/06 la devolución RESTABA (signo = -1,
                # "Paso 1.8, sin confirmar con el cliente"). Con el cambio
                # directo confirmado eso generaba dos diferencias falsas
                # por cada cambio — el lleno que salía quedaba sin
                # registrar y encima se descontaba uno más.
                if linea.tipo == "venta" or (
                    linea.tipo == "devolucion" and linea.cambio_directo
                ):
                    registrado[linea.product_id] = (
                        registrado.get(linea.product_id, 0.0) + linea.cantidad
                    )
        productos = set(salidas) | set(entradas) | set(registrado)
        for producto in productos:
            salida = salidas.get(producto, 0.0)
            entrada = entradas.get(producto, 0.0)
            self.env["insa.liquidacion.articulo"].create(
                {
                    "hoja_ruta_id": self.id,
                    "product_id": producto.id,
                    "salida": salida,
                    "entrada": entrada,
                    "teorico": salida - entrada,
                    "registrado": registrado.get(producto, 0.0),
                }
            )

    def _crear_liquidacion_envases(self):
        salidas, prestamos, devoluciones, entrada_real = {}, {}, {}, {}
        for playa in self.control_playa_ids:
            for linea in playa.linea_ids:
                if not linea.product_id.es_retornable:
                    continue
                if playa.tipo == "salida":
                    salidas[linea.product_id] = (
                        salidas.get(linea.product_id, 0.0) + linea.cantidad
                    )
                else:
                    # RN-08 actualizada por C-05: entrada_real = llenos +
                    # vacíos + fallados. El fallado es un envase que
                    # volvió — cuenta para el saldo de envases aunque su
                    # contenido no reingrese a stock vendible.
                    entrada_real[linea.product_id] = entrada_real.get(
                        linea.product_id, 0.0
                    ) + (linea.cantidad + linea.cantidad_vacia + linea.cantidad_fallada)
        for mov in self.movimiento_cliente_ids:
            for linea in mov.linea_envase_ids:
                destino = prestamos if linea.tipo == "prestamo" else devoluciones
                destino[linea.product_id] = (
                    destino.get(linea.product_id, 0.0) + linea.cantidad
                )
        productos = set(salidas) | set(prestamos) | set(devoluciones) | set(entrada_real)
        for producto in productos:
            salida = salidas.get(producto, 0.0)
            prestado = prestamos.get(producto, 0.0)
            devuelto = devoluciones.get(producto, 0.0)
            self.env["insa.liquidacion.envase"].create(
                {
                    "hoja_ruta_id": self.id,
                    "product_id": producto.id,
                    "salida": salida,
                    "prestamos": prestado,
                    "devoluciones": devuelto,
                    "entrada_teorica": salida - prestado + devuelto,
                    "entrada_real": entrada_real.get(producto, 0.0),
                }
            )

    def _crear_liquidacion_fallados(self):
        """C-07 / RN-24: el producto no conforme que el repartidor cargó
        en la visita del cliente tiene que matchear con los fallados que
        el controlador contó al volver.

            fallados_registrados = Σ devoluciones cargadas en las visitas
            fallados_contados    = Σ cantidad_fallada del control de ENTRADA
            discordancia         = registrados − contados

        Textual de Nico: "si pone que era vacío, va a decir: che, ¿qué
        pasa? Hay discordancia entre la información del distribuidor y la
        información del control de playa"."""
        registrados, contados = {}, {}
        for mov in self.movimiento_cliente_ids:
            for linea in mov.linea_articulo_ids.filtered(lambda l: l.tipo == "devolucion"):
                registrados[linea.product_id] = (
                    registrados.get(linea.product_id, 0.0) + linea.cantidad
                )
        for playa in self.control_playa_ids.filtered(lambda c: c.tipo == "entrada"):
            for linea in playa.linea_ids.filtered("cantidad_fallada"):
                contados[linea.product_id] = (
                    contados.get(linea.product_id, 0.0) + linea.cantidad_fallada
                )
        for producto in set(registrados) | set(contados):
            self.env["insa.liquidacion.fallado"].create(
                {
                    "hoja_ruta_id": self.id,
                    "product_id": producto.id,
                    "registrados": registrados.get(producto, 0.0),
                    "contados": contados.get(producto, 0.0),
                }
            )

    control_salida_aprobado = fields.Boolean(
        string="Control de salida aprobado",
        compute="_compute_controles_aprobados",
        help="R-09 / RN-23: sin el control de salida aprobado por un "
        "controlador no se registra ninguna visita — es el bloqueo que "
        "antes daba el estado Borrador.",
    )
    control_entrada_aprobado = fields.Boolean(
        string="Control de entrada aprobado",
        compute="_compute_controles_aprobados",
        help="RN-23: hasta que el controlador no aprueba el conteo del "
        "regreso, la liquidación no arroja resultados definitivos.",
    )

    @api.depends("control_playa_ids.tipo", "control_playa_ids.state")
    def _compute_controles_aprobados(self):
        for rec in self:
            aprobados = rec.control_playa_ids.filtered(lambda c: c.state == "confirmado")
            rec.control_salida_aprobado = "salida" in aprobados.mapped("tipo")
            rec.control_entrada_aprobado = "entrada" in aprobados.mapped("tipo")

    def _check_administrativa_cierre(self):
        # R-03 / R-07: "la diferencia de pérdida únicamente tiene
        # autorización Camila". Declarar el efectivo y finalizar son de
        # ella; ni el repartidor, ni el controlador, ni el supervisor.
        if not self.env.user.has_group("insa_reparto.group_administrativa_cierre"):
            raise UserError(
                _(
                    "Solo la administrativa de cierre puede declarar el "
                    "efectivo y finalizar el reparto."
                )
            )

    def _check_visitas_editables(self):
        """R-02 / R-06: las visitas (y sus líneas) se cargan libremente
        mientras la hoja está en reparto. Consolidando, solo la
        administrativa de cierre las corrige desde la web ("agregar una
        venta extra a un cliente para corregir una carga olvidada").
        Cerrada, ya impactaron en el cliente: no se tocan más."""
        for rec in self:
            if rec.state == "cerrada":
                raise UserError(
                    _("La hoja de ruta %s está cerrada: sus visitas ya no se modifican.")
                    % rec.display_name
                )
            if rec.state == "consolidando" and not self.env.user.has_group(
                "insa_reparto.group_administrativa_cierre"
            ):
                raise UserError(
                    _(
                        "La hoja de ruta %s está consolidando: solo la "
                        "administrativa de cierre puede corregir sus visitas."
                    )
                    % rec.display_name
                )

    def _registrar_correccion_web(self, detalle):
        """R-06: "todo cambio queda auditado (quién, cuándo, antes y
        después)" — y la liquidación se recalcula, así el renglón amarillo
        desaparece apenas se carga la venta que faltaba. Solo aplica a
        correcciones sobre una hoja ya consolidando; durante el reparto la
        carga es la normal del día."""
        for rec in self.filtered(lambda h: h.state == "consolidando"):
            rec._message_log(
                body=_("Corrección desde la web por %(user)s: %(detalle)s")
                % {"user": self.env.user.name, "detalle": detalle}
            )
            rec.action_recalcular_liquidacion()

    def _check_puede_declarar_efectivo(self):
        for rec in self:
            if rec.state == "cerrada":
                raise UserError(_("La hoja de ruta %s ya está cerrada.") % rec.name)
            # RN-23: la liquidación (y por lo tanto el efectivo cobrado
            # contra el que se declara) no es definitiva hasta que el
            # controlador aprueba el conteo del regreso.
            if not rec.control_entrada_aprobado:
                raise UserError(
                    _(
                        "Falta el control de playa de ENTRADA aprobado: el "
                        "efectivo se declara con el camión ya controlado."
                    )
                )

    def action_abrir_wizard_declarar_efectivo(self):
        self.ensure_one()
        self._check_administrativa_cierre()
        self._check_puede_declarar_efectivo()
        return {
            "type": "ir.actions.act_window",
            "name": _("Declarar efectivo"),
            "res_model": "insa.declarar.efectivo.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_hoja_ruta_id": self.id},
        }

    def _declarar_efectivo(self, lineas):
        """R-01 / R-04: la administrativa de cierre carga el ticket del
        cajero de depósito billete por billete — `lineas` es una lista de
        (denominación, cantidad). Es el disparador de "Consolidando" (no
        el repartidor al entregar la plata). Se puede volver a declarar
        mientras la hoja no esté cerrada: reemplaza la declaración
        anterior."""
        self.ensure_one()
        self._check_administrativa_cierre()
        self._check_puede_declarar_efectivo()
        lineas = [(denominacion, cantidad) for denominacion, cantidad in lineas if cantidad]
        total = sum(denominacion * cantidad for denominacion, cantidad in lineas)
        self.write(
            {
                "declaracion_efectivo_ids": [
                    Command.unlink(l.id) for l in self.declaracion_efectivo_ids
                ]
                + [
                    Command.create(
                        {"denominacion": denominacion, "cantidad": cantidad, "rol": "declarante"}
                    )
                    for denominacion, cantidad in lineas
                ],
                "efectivo_caja_repartidor": total,
                "efectivo_declarado_por_id": self.env.uid,
                "efectivo_declarado_fecha": fields.Datetime.now(),
                "state": "consolidando",
                # 4.7: si se corrige la declaración, la verificación
                # anterior del controlador queda invalidada — no se cierra
                # con un segundo control de un monto que ya cambió.
                "declaracion_efectivo_controlador_ids": [
                    Command.unlink(l.id) for l in self.declaracion_efectivo_controlador_ids
                ],
                "efectivo_contado_controlador": 0.0,
                "controlador_verifico_por_id": False,
                "controlador_verifico_fecha": False,
            }
        )
        self.action_recalcular_liquidacion()
        self._message_log(
            body=_(
                "Efectivo declarado: $ %(declarado)s (cobrado según visitas: "
                "$ %(cobrado)s)."
            )
            % {
                "declarado": "{:,.2f}".format(total),
                "cobrado": "{:,.2f}".format(self.total_efectivo_registrado),
            }
        )
        return True

    def action_abrir_wizard_verificar_efectivo(self):
        self.ensure_one()
        if self.state != "consolidando":
            raise UserError(
                _(
                    "El efectivo se verifica después de que la "
                    "administrativa lo haya declarado."
                )
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Verificar efectivo"),
            "res_model": "insa.verificar.efectivo.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_hoja_ruta_id": self.id},
        }

    def _verificar_efectivo_controlador(self, login, password, lineas):
        """4.7: punto de entrada público, usado por el wizard. Separado de
        _verificar_efectivo_como porque res.users.authenticate() abre su
        propio cursor: dentro de un test no ve los usuarios creados en la
        transacción, así que la suite entra directo por
        _verificar_efectivo_como (mismo criterio que action_aprobar/
        _aprobar_como en insa.control.playa, C-08/C-09)."""
        self.ensure_one()
        try:
            auth_info = self.env["res.users"].sudo().authenticate(
                self.env.cr.dbname,
                {"type": "password", "login": login, "password": password},
                {"interactive": False},
            )
        except AccessDenied as exc:
            raise UserError(_("Usuario o contraseña incorrectos.")) from exc
        controlador = self.env["res.users"].sudo().browse(auth_info["uid"])
        return self._verificar_efectivo_como(controlador, lineas)

    def _verificar_efectivo_como(self, controlador, lineas):
        """La verificación en sí, con el controlador ya identificado —
        segundo control, independiente de la declaración de la
        administrativa. La escritura corre con la sesión de quien abrió
        el wizard (la administrativa, en el mostrador), no con la del
        controlador: él solo acreditó su identidad. Se puede repetir
        mientras la hoja no esté cerrada, igual que la declaración."""
        self.ensure_one()
        if self.state != "consolidando":
            raise UserError(
                _(
                    "El efectivo se verifica después de que la "
                    "administrativa lo haya declarado."
                )
            )
        if not controlador.has_group("insa_reparto.group_controlador"):
            raise UserError(
                _(
                    "%s no tiene permiso de Controlador de Playa: no puede "
                    "verificar el efectivo."
                )
                % controlador.name
            )
        lineas = [(denominacion, cantidad) for denominacion, cantidad in lineas if cantidad]
        total = sum(denominacion * cantidad for denominacion, cantidad in lineas)
        self.write(
            {
                "declaracion_efectivo_controlador_ids": [
                    Command.unlink(l.id) for l in self.declaracion_efectivo_controlador_ids
                ]
                + [
                    Command.create(
                        {"denominacion": denominacion, "cantidad": cantidad, "rol": "controlador"}
                    )
                    for denominacion, cantidad in lineas
                ],
                "efectivo_contado_controlador": total,
                "controlador_verifico_por_id": controlador.id,
                "controlador_verifico_fecha": fields.Datetime.now(),
            }
        )
        self._message_log(
            body=_("Efectivo verificado por %(controlador)s: $ %(total)s.")
            % {"controlador": controlador.name, "total": "{:,.2f}".format(total)}
        )
        return True

    def action_finalizar_reparto(self):
        """R-02: "Finalizar reparto" confirma todo en bloque. Sin
        diferencias cierra directo; con renglones en amarillo o diferencia
        de efectivo abre el cierre con diferencias, donde cada una queda
        registrada como pérdida con su motivo (R-07)."""
        self.ensure_one()
        self._check_administrativa_cierre()
        if self.state != "consolidando":
            raise UserError(
                _(
                    "Solo se finaliza un reparto en 'Consolidando': primero "
                    "hay que declarar el efectivo."
                )
            )
        # 4.7: el segundo control (otra persona, el controlador de playa)
        # es obligatorio antes de cerrar — mismo criterio que RN-22 para
        # la aprobación del control de playa.
        if not self.controlador_verifico_por_id:
            raise UserError(
                _(
                    "Falta la verificación del efectivo por un controlador "
                    "de playa: no se puede finalizar sin el segundo control."
                )
            )
        self.action_recalcular_liquidacion()
        if self.tiene_diferencias:
            return {
                "type": "ir.actions.act_window",
                "name": _("Finalizar con diferencias"),
                "res_model": "insa.cerrar.con.diferencias.wizard",
                "view_mode": "form",
                "target": "new",
                "context": {"default_hoja_ruta_id": self.id},
            }
        self._finalizar()
        return True

    def _finalizar(self):
        """R-02: "hasta que el reparto no dice cerrado, nada de lo que
        haya cargado el repartidor efectivamente migra a la cuenta del
        cliente". Al cerrar, en bloque:
          - los préstamos/devoluciones de envase mueven el saldo de
            envases del cliente (stock.move, action_confirmar);
          - las ventas pasan a un pedido de venta confirmado por cliente,
            sin facturar (decisión del 24/09: la facturación mensual F-04
            lo toma después; durante el piloto se sigue facturando en el
            sistema viejo, A-2);
          - FUE/FUC se recalculan solos al pasar la hoja a cerrada (ver
            res.partner._compute_fue_fuc).
        Los cobros en efectivo quedan en la visita, sin asiento contable,
        hasta que se defina la convivencia con la facturación del sistema
        viejo (A-2)."""
        for rec in self:
            movimientos = rec.movimiento_cliente_ids.sudo()
            movimientos.filtered(lambda m: not m.envase_movido).action_confirmar()
            movimientos._crear_pedido_venta()
            rec.write({"state": "cerrada"})
            rec._message_log(body=_("Reparto finalizado: ventas, envases y FUE/FUC confirmados."))
        return True

    def _preparar_perdidas(self):
        """R-07: una línea por cada diferencia que queda sin resolver al
        finalizar — la administrativa le asigna el motivo a cada una. El
        importe es una estimación editable (cantidad × precio de lista);
        en efectivo es la diferencia misma. Signo: positivo es faltante,
        negativo es sobrante."""
        self.ensure_one()
        motivo_efectivo = self.env.ref(
            "insa_reparto.motivo_perdida_diferencia_efectivo", raise_if_not_found=False
        )
        motivo_envase = self.env.ref(
            "insa_reparto.motivo_perdida_envase_no_recuperado", raise_if_not_found=False
        )
        vals = []
        for linea in self.liquidacion_articulo_ids.filtered("diferencia"):
            vals.append(
                {
                    "tipo": "articulo",
                    "product_id": linea.product_id.id,
                    "cantidad": linea.diferencia,
                    "importe": linea.diferencia * linea.product_id.list_price,
                }
            )
        for linea in self.liquidacion_envase_ids.filtered("diferencia"):
            vals.append(
                {
                    "tipo": "envase",
                    "product_id": linea.product_id.id,
                    "cantidad": linea.diferencia,
                    "importe": linea.diferencia * linea.product_id.envase_id.list_price,
                    "motivo_id": motivo_envase.id if motivo_envase else False,
                }
            )
        for linea in self.liquidacion_fallado_ids.filtered("discordancia"):
            vals.append(
                {
                    "tipo": "fallado",
                    "product_id": linea.product_id.id,
                    "cantidad": linea.discordancia,
                    "importe": linea.discordancia * linea.product_id.list_price,
                }
            )
        if self.diferencia_efectivo:
            vals.append(
                {
                    "tipo": "efectivo",
                    "importe": self.diferencia_efectivo,
                    "motivo_id": motivo_efectivo.id if motivo_efectivo else False,
                }
            )
        return vals

    def _finalizar_con_diferencias(self, perdidas, observacion):
        """R-07 / RN-14: toda liquidación se cierra. "Le sobraron mil
        pesos; por mil pesos no vamos a dejar una hoja de ruta pendiente
        de cerrar" — pero cada diferencia queda en el diario de pérdidas
        con su motivo, para poder reportar y tomar acciones."""
        self.ensure_one()
        self._check_administrativa_cierre()
        if self.state != "consolidando":
            raise UserError(_("Solo se finaliza un reparto en 'Consolidando'."))
        if not observacion:
            raise UserError(_("La observación es obligatoria para finalizar con diferencias."))
        if any(not perdida.get("motivo_id") for perdida in perdidas):
            raise UserError(_("Cada diferencia necesita su motivo."))
        self.env["insa.reparto.perdida"].create(
            [dict(perdida, hoja_ruta_id=self.id, observacion=perdida.get("observacion") or observacion)
             for perdida in perdidas]
        )
        self.write(
            {
                "close_forced": True,
                "close_forced_reason": observacion,
                "close_forced_by": self.env.user.id,
                "close_forced_date": fields.Datetime.now(),
            }
        )
        self._message_log(body=_("Reparto finalizado con diferencias. Observación: %s") % observacion)
        # sudo: el ajuste de stock del envase lo genera el sistema, ya
        # validado el permiso de arriba — la administrativa no necesita
        # permisos de inventario. "Autorizado por" sigue siendo ella.
        self.sudo()._generar_ajustes_inventario(observacion)
        self._finalizar()
        return True

    def _generar_ajustes_inventario(self, motivo):
        """R-6.4: toda diferencia no resuelta queda auditada en
        insa.ajuste.inventario (quién, cuándo, motivo, cuánto).

        Solo para ENVASES se corrige además la cantidad física real en
        stock (con el mecanismo estándar de ajuste de inventario de
        Odoo, stock.quant.inventory_quantity) — el envase es el único
        producto que este módulo obliga a trackear en stock (ver
        product.template._check_envase_es_storable). Para ARTÍCULOS no
        se genera movimiento: el conteo de playa (salida/entrada) ya ES
        la cantidad física real; lo que falta ahí es el registro de la
        venta, no una corrección de inventario, así que queda solo
        como auditoría.

        El asiento contable contra una cuenta de mermas/faltantes
        [PARA INSA] no se implementa acá — decisión del checkpoint del
        06/08/2026, queda pendiente."""
        Ajuste = self.env["insa.ajuste.inventario"]
        for rec in self:
            for linea in rec.liquidacion_articulo_ids.filtered("diferencia"):
                Ajuste.create(
                    {
                        "hoja_ruta_id": rec.id,
                        "tipo": "articulo",
                        "product_id": linea.product_id.id,
                        "diferencia": linea.diferencia,
                        "motivo": motivo,
                    }
                )
            for linea in rec.liquidacion_envase_ids.filtered("diferencia"):
                move = linea._ajustar_stock_envase()
                Ajuste.create(
                    {
                        "hoja_ruta_id": rec.id,
                        "tipo": "envase",
                        "product_id": linea.product_id.id,
                        "diferencia": linea.diferencia,
                        "motivo": motivo,
                        "stock_move_id": move.id if move else False,
                    }
                )
            # C-07: la discordancia de fallados también se audita. Igual
            # que en artículos, no genera movimiento de stock — lo que
            # falta ahí es el registro de la devolución, no una corrección
            # de inventario.
            for linea in rec.liquidacion_fallado_ids.filtered("discordancia"):
                Ajuste.create(
                    {
                        "hoja_ruta_id": rec.id,
                        "tipo": "fallado",
                        "product_id": linea.product_id.id,
                        "diferencia": linea.discordancia,
                        "motivo": motivo,
                    }
                )


class InsaCerrarConDiferenciasWizard(models.TransientModel):
    _name = "insa.cerrar.con.diferencias.wizard"
    _description = "Finalizar reparto con diferencias (pérdidas con motivo)"

    hoja_ruta_id = fields.Many2one(comodel_name="fsm.route.dayroute", required=True)
    motivo = fields.Text(string="Observación", required=True)
    linea_ids = fields.One2many(
        comodel_name="insa.cerrar.con.diferencias.wizard.linea",
        inverse_name="wizard_id",
        string="Diferencias",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        hoja = self.env["fsm.route.dayroute"].browse(res.get("hoja_ruta_id"))
        if hoja and "linea_ids" in fields_list:
            res["linea_ids"] = [Command.create(vals) for vals in hoja._preparar_perdidas()]
        return res

    def action_confirmar(self):
        self.ensure_one()
        self.hoja_ruta_id._finalizar_con_diferencias(
            [
                {
                    "tipo": linea.tipo,
                    "product_id": linea.product_id.id,
                    "cantidad": linea.cantidad,
                    "importe": linea.importe,
                    "motivo_id": linea.motivo_id.id,
                    "observacion": linea.observacion,
                }
                for linea in self.linea_ids
            ],
            self.motivo,
        )
        return {"type": "ir.actions.act_window_close"}


class InsaCerrarConDiferenciasWizardLinea(models.TransientModel):
    _name = "insa.cerrar.con.diferencias.wizard.linea"
    _description = "Diferencia a registrar como pérdida al finalizar"

    wizard_id = fields.Many2one(
        comodel_name="insa.cerrar.con.diferencias.wizard", required=True, ondelete="cascade"
    )
    # Solo lectura en la vista (con force_save), no en el modelo: un campo
    # readonly de modelo no viaja al guardar el wizard y 'tipo' quedaría
    # vacío.
    tipo = fields.Selection(selection=TIPOS_PERDIDA, required=True)
    product_id = fields.Many2one(comodel_name="product.template", string="Producto")
    cantidad = fields.Float()
    importe = fields.Float()
    motivo_id = fields.Many2one(
        comodel_name="insa.reparto.perdida.motivo", string="Motivo", required=True
    )
    observacion = fields.Char(string="Observación")


class InsaLiquidacionArticulo(models.Model):
    _name = "insa.liquidacion.articulo"
    _description = "Línea de liquidación de artículos"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(comodel_name="product.template", string="Producto")
    # C-16: las cinco columnas del cuadro tal como lo lee INSA. Salida y
    # entrada ya se calculaban dentro de _crear_liquidacion_articulos y se
    # tiraban; ahora se persisten, porque son parte del pantallazo rápido.
    salida = fields.Float(string="Salida")
    entrada = fields.Float(string="Entrada")
    teorico = fields.Float(string="Teórico entregado")
    registrado = fields.Float(
        string="Registrado",
        help="C-16: la columna de lectura rápida — 'cuántos registrados "
        "hay: eso nos da un parámetro rápido de cuánto es lo que vendió la "
        "persona'. Incluye las ventas y los cambios directos (C-06), que "
        "no son venta pero sí salieron del camión.",
    )
    diferencia = fields.Float(string="Diferencia", compute="_compute_diferencia", store=True)
    # 4.8 (revisión 29/09): campos de solo lectura, no almacenados, para
    # leer la liquidación sin abrir cada línea — no tocan la fórmula de
    # diferencia (RN-06), solo la presentan con color y texto.
    estado = fields.Selection(
        selection=[("ok", "OK"), ("revisar", "Revisar")],
        string="Estado",
        compute="_compute_estado_texto",
    )
    # string distinto del campo "diferencia"/"discordancia" (ambos se
    # muestran como "Diferencia" en la vista vía <field string="...">) —
    # evita el warning de Odoo por dos campos con la misma etiqueta.
    diferencia_texto = fields.Char(
        string="Diferencia (texto)", compute="_compute_estado_texto"
    )

    @api.depends("teorico", "registrado")
    def _compute_diferencia(self):
        # RN-06: diferencia = teórico entregado − registrado (no al revés).
        for rec in self:
            rec.diferencia = rec.teorico - rec.registrado

    @api.depends("diferencia")
    def _compute_estado_texto(self):
        for rec in self:
            rec.estado = "ok" if rec.diferencia == 0 else "revisar"
            if rec.diferencia > 0:
                rec.diferencia_texto = _("Falta registrar %g") % rec.diferencia
            elif rec.diferencia < 0:
                rec.diferencia_texto = _("Sobran %g") % -rec.diferencia
            else:
                rec.diferencia_texto = _("OK")


class InsaLiquidacionEnvase(models.Model):
    _name = "insa.liquidacion.envase"
    _description = "Línea de liquidación de envases"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(comodel_name="product.template", string="Producto retornable")
    # C-16: 'con cuántos envases salió, y los préstamos y devoluciones:
    # eso nos sirve como pantallazo rápido de cuál es el manejo que está
    # teniendo nuestro repartidor de los envases'.
    salida = fields.Float(string="Salida")
    prestamos = fields.Float(string="Préstamos")
    devoluciones = fields.Float(string="Devoluciones")
    entrada_teorica = fields.Float(string="Entrada teórica")
    entrada_real = fields.Float(
        string="Entrada real",
        help="C-05: llenos + vacíos + fallados del control de entrada.",
    )
    diferencia = fields.Float(string="Diferencia", compute="_compute_diferencia", store=True)
    estado = fields.Selection(
        selection=[("ok", "OK"), ("revisar", "Revisar")],
        string="Estado",
        compute="_compute_estado_texto",
    )
    # string distinto del campo "diferencia"/"discordancia" (ambos se
    # muestran como "Diferencia" en la vista vía <field string="...">) —
    # evita el warning de Odoo por dos campos con la misma etiqueta.
    diferencia_texto = fields.Char(
        string="Diferencia (texto)", compute="_compute_estado_texto"
    )

    @api.depends("entrada_teorica", "entrada_real")
    def _compute_diferencia(self):
        # RN-07/RN-08: diferencia = entrada teórica − entrada real.
        for rec in self:
            rec.diferencia = rec.entrada_teorica - rec.entrada_real

    @api.depends("diferencia")
    def _compute_estado_texto(self):
        for rec in self:
            rec.estado = "ok" if rec.diferencia == 0 else "revisar"
            if rec.diferencia > 0:
                rec.diferencia_texto = _("Falta registrar %g") % rec.diferencia
            elif rec.diferencia < 0:
                rec.diferencia_texto = _("Sobran %g") % -rec.diferencia
            else:
                rec.diferencia_texto = _("OK")

    def _ajustar_stock_envase(self):
        """R-6.4: corrige la cantidad física del envase en la ubicación
        del camión para que quede acorde al conteo real verificado en
        el control de playa. Usa el mecanismo estándar de ajuste de
        inventario de Odoo (stock.quant.inventory_quantity) — sin
        tocar contabilidad. Devuelve el stock.move generado, o False
        si no hay envase trackeable o no hay diferencia."""
        self.ensure_one()
        if not self.diferencia:
            return False
        envase = self.product_id.envase_id
        if not envase or not envase.is_storable:
            return False
        variante = envase.product_variant_id
        camion = self.env["stock.warehouse"].search([], limit=1).lot_stock_id
        quant = self.env["stock.quant"]._gather(variante, camion, strict=True)[:1]
        if not quant:
            quant = self.env["stock.quant"].create(
                {"product_id": variante.id, "location_id": camion.id}
            )
        # diferencia = entrada_teórica − entrada_real (RN-07/08): si es
        # positiva falta stock físico, si es negativa sobra.
        quant = quant.with_context(inventory_mode=True)
        quant.inventory_quantity = quant.quantity - self.diferencia
        quant.action_apply_inventory()
        # action_apply_inventory() no devuelve ni linkea el stock.move que
        # genera; lo recuperamos por búsqueda (solo para dejarlo linkeado
        # en la auditoría, no afecta el ajuste en sí, que ya se aplicó).
        return self.env["stock.move"].search(
            [
                ("product_id", "=", variante.id),
                ("state", "=", "done"),
                "|",
                ("location_id", "=", camion.id),
                ("location_dest_id", "=", camion.id),
            ],
            order="id desc",
            limit=1,
        )


class InsaLiquidacionFallado(models.Model):
    """C-07: conciliación de producto no conforme. Es el tercer cuadro de
    la liquidación, al lado de artículos y envases: cruza lo que el
    repartidor dijo que le devolvieron contra lo que el controlador contó
    como fallado al volver el camión."""

    _name = "insa.liquidacion.fallado"
    _description = "Línea de conciliación de producto fallado"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(comodel_name="product.template", string="Producto")
    registrados = fields.Float(
        string="Registrados en visitas",
        help="Σ de las devoluciones de artículo no conforme que el "
        "repartidor cargó cliente por cliente.",
    )
    contados = fields.Float(
        string="Contados en playa",
        help="Σ de la cantidad fallada del control de playa de ENTRADA.",
    )
    discordancia = fields.Float(
        string="Discordancia", compute="_compute_discordancia", store=True
    )
    estado = fields.Selection(
        selection=[("ok", "OK"), ("revisar", "Revisar")],
        string="Estado",
        compute="_compute_estado_texto",
    )
    # string distinto del campo "diferencia"/"discordancia" (ambos se
    # muestran como "Diferencia" en la vista vía <field string="...">) —
    # evita el warning de Odoo por dos campos con la misma etiqueta.
    diferencia_texto = fields.Char(
        string="Diferencia (texto)", compute="_compute_estado_texto"
    )

    @api.depends("registrados", "contados")
    def _compute_discordancia(self):
        for rec in self:
            rec.discordancia = rec.registrados - rec.contados

    @api.depends("discordancia")
    def _compute_estado_texto(self):
        for rec in self:
            rec.estado = "ok" if rec.discordancia == 0 else "revisar"
            if rec.discordancia > 0:
                rec.diferencia_texto = _(
                    "Repartidor registró %g de más"
                ) % rec.discordancia
            elif rec.discordancia < 0:
                rec.diferencia_texto = _(
                    "Playa contó %g de más"
                ) % -rec.discordancia
            else:
                rec.diferencia_texto = _("OK")
