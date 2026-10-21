# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""C-18 · Pantalla "Repartos / Orden de clientes" — cierra el vacío V-02
de la spec v1 (D-01: "el orden de la hoja de ruta no es por proximidad ni
optimizado: es manual, heredado, y se administra en una pantalla
dedicada").

Textual de la reunión del 08/06: "el orden no corresponde a la
aproximación de mi ubicación con el cliente, sino a cómo se haya tomado
el trabajo de ordenarlo el repartidor y el supervisor... la hoja de ruta
del lunes siempre es la misma, la del martes siempre es la misma."

insa.ruta.plantilla (reparto + día de la semana) es la "columna
vertebral" fija de ese reparto ese día — insa.ruta.plantilla.line es cada
cliente con su lugar en la fila. La hoja de ruta diaria concreta
(fsm.route.dayroute) se genera a partir de acá (Etapa 0), no al revés.

No se adaptó fsm.recurring (OCA fieldservice_recurring, ya declarado
como dependencia) para esto: ese modelo es una recurrencia POR CLIENTE
con su propia frecuencia, pensado para "a este cliente generale un
pedido cada N días" — no tiene noción de "posición dentro de la fila del
reparto del día", que es el pedido central de C-18 (dos listas
enfrentadas, subir/bajar/insertar). Forzarlo ahí habría sido más
complejo que el modelo chico y de un solo propósito que sugiere el
propio documento D1.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class InsaRutaPlantilla(models.Model):
    _name = "insa.ruta.plantilla"
    _inherit = ["mail.thread"]
    _description = "Plantilla de ruta fija (reparto + día de la semana)"
    _rec_name = "display_name"

    route_id = fields.Many2one(
        comodel_name="fsm.route", string="Reparto / móvil", required=True
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
        required=True,
    )
    line_ids = fields.One2many(
        comodel_name="insa.ruta.plantilla.line",
        inverse_name="plantilla_id",
        string="Clientes",
    )
    # C-18 / RN-26: dos listas enfrentadas — "sin ordenar" (secuencia 0) y
    # "ordenados" (secuencia 1..N). Dos One2many con domain propio en
    # Python, no domain="..." puesto solo en la vista: ya se comprobó en
    # este módulo (Sección 2, pestañas Cobro en efectivo/Préstamos y
    # devoluciones) que reusar el mismo nombre de campo con distinto
    # domain solo-en-XML no filtra de forma confiable en el cliente web
    # de Odoo 18.
    line_sin_ordenar_ids = fields.One2many(
        comodel_name="insa.ruta.plantilla.line",
        inverse_name="plantilla_id",
        domain=[("secuencia", "=", 0), ("activo", "=", True)],
        string="Clientes sin ordenar",
    )
    line_ordenadas_ids = fields.One2many(
        comodel_name="insa.ruta.plantilla.line",
        inverse_name="plantilla_id",
        domain=[("secuencia", ">", 0), ("activo", "=", True)],
        string="Clientes ordenados",
    )
    cantidad_clientes = fields.Integer(
        string="Clientes activos", compute="_compute_cantidad_clientes"
    )

    _sql_constraints = [
        (
            "route_dia_unique",
            "unique(route_id, dia_semana)",
            "Ya existe una plantilla para ese reparto en ese día de la semana.",
        ),
    ]

    @api.depends("line_ids.activo")
    def _compute_cantidad_clientes(self):
        for rec in self:
            rec.cantidad_clientes = len(rec.line_ids.filtered("activo"))

    @api.depends("route_id.numero_reparto", "dia_semana")
    def _compute_display_name(self):
        dias = dict(self._fields["dia_semana"].selection)
        for rec in self:
            numero = rec.route_id.numero_reparto or rec.route_id.name or "?"
            rec.display_name = _("Reparto %(numero)s — %(dia)s") % {
                "numero": numero,
                "dia": dias.get(rec.dia_semana, rec.dia_semana or ""),
            }

    def action_agregar_cliente(self, partner_id):
        """Alta rápida (C-20) y altas manuales entran siempre como 'sin
        ordenar' (RN-27) — es el repartidor quien, al final de la ruta,
        decide dónde va el cliente nuevo. Devuelve el id de la línea (no
        el recordset): quien la llama nunca lo necesita como recordset
        (el controlador lo descarta) y así también se puede invocar por
        XML-RPC sin que falle el marshalling de la respuesta."""
        self.ensure_one()
        existente = self.line_ids.filtered(lambda l: l.partner_id.id == partner_id)
        if existente:
            if not existente.activo:
                existente.write({"activo": True, "secuencia": 0})
            return existente.id
        linea = self.env["insa.ruta.plantilla.line"].create(
            {"plantilla_id": self.id, "partner_id": partner_id, "secuencia": 0}
        )
        return linea.id

    def action_generar_hoja_ruta(self, fecha):
        """Etapa 0: copia la plantilla a la hoja de ruta concreta de esa
        fecha. Crea el fsm.route.dayroute si no existe, y un fsm.order
        (parada programada) por cada cliente ordenado (secuencia > 0),
        respetando ese orden — los "sin ordenar" no entran solos, hay
        que ordenarlos primero.

        Idempotente: si la hoja ya tiene una parada para ese cliente, no
        la duplica (se puede correr de nuevo sin miedo, por ejemplo si se
        reordenó la plantilla después de generar la hoja del día por
        error)."""
        self.ensure_one()
        # fields.Date.to_date() acepta tanto un date de Python (llamada
        # interna, ej. action_generar_hoja_ruta_hoy) como el string ISO
        # que llega si esto se invoca por XML-RPC/JSON-RPC — un date
        # crudo no se convierte solo al cruzar ese límite.
        fecha = fields.Date.to_date(fecha)
        if self.dia_semana != str(fecha.weekday()):
            raise UserError(
                _(
                    "Esta plantilla es de los %(dia)s y %(fecha)s no cae ese día."
                )
                % {"dia": dict(self._fields["dia_semana"].selection)[self.dia_semana],
                   "fecha": fecha.strftime("%d/%m/%Y")}
            )
        Dayroute = self.env["fsm.route.dayroute"]
        hoja = Dayroute.search(
            [("route_id", "=", self.route_id.id), ("date", "=", fecha)], limit=1
        )
        if not hoja:
            hoja = Dayroute.create({"route_id": self.route_id.id, "date": fecha})
        return self._poblar_pedidos_en_hoja(hoja)

    def _poblar_pedidos_en_hoja(self, hoja):
        """4.5 (revisión 29/09): separado de action_generar_hoja_ruta para
        que fsm.route.dayroute.create() pueda poblar la hoja que acaba de
        crear directamente — sin esto, si ya existe otra hoja de ruta
        para la misma ruta+fecha (puede pasar: fsm.route.dayroute no
        tiene restricción de unicidad), el search() de más arriba
        encuentra esa OTRA hoja y los pedidos se cargan ahí, dejando
        vacía la hoja nueva que el usuario está mirando."""
        self.ensure_one()
        Order = self.env["fsm.order"]
        ya_en_hoja = hoja.order_ids.mapped("location_id.partner_id")
        lineas = self.line_ordenadas_ids.sorted("secuencia")
        creadas = 0
        inactivos = 0
        for linea in lineas:
            if linea.partner_id in ya_en_hoja:
                continue
            # R-08: la plantilla conserva a todos (activos y bajas), pero
            # la hoja del día trae solo a los activos.
            if not linea.partner_id._insa_activo_para_reparto():
                inactivos += 1
                continue
            location = self.env["fsm.location"].search(
                [("partner_id", "=", linea.partner_id.id)], limit=1
            )
            if not location:
                continue
            Order.create(
                {
                    "location_id": location.id,
                    "fsm_route_id": self.route_id.id,
                    "dayroute_id": hoja.id,
                    "sequence": linea.secuencia,
                }
            )
            creadas += 1
        return {
            "hoja_ruta_id": hoja.id,
            "paradas_creadas": creadas,
            "clientes_inactivos": inactivos,
        }

    def action_generar_hoja_ruta_hoy(self):
        """Botón de la pantalla: genera (o completa) la hoja de ruta de
        hoy a partir de esta plantilla y abre esa hoja de ruta."""
        self.ensure_one()
        resultado = self.action_generar_hoja_ruta(fields.Date.context_today(self))
        self.message_post(
            body=_(
                "Hoja de ruta de hoy generada: %(creadas)s paradas nuevas desde "
                "esta plantilla (%(inactivos)s clientes no activos quedaron afuera)."
            )
            % {
                "creadas": resultado["paradas_creadas"],
                "inactivos": resultado["clientes_inactivos"],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.route.dayroute",
            "res_id": resultado["hoja_ruta_id"],
            "view_mode": "form",
            "target": "current",
        }


class InsaRutaPlantillaLine(models.Model):
    _name = "insa.ruta.plantilla.line"
    _description = "Cliente dentro de una plantilla de ruta"
    _order = "secuencia, id"

    plantilla_id = fields.Many2one(
        comodel_name="insa.ruta.plantilla", required=True, ondelete="cascade"
    )
    partner_id = fields.Many2one(comodel_name="res.partner", string="Cliente", required=True)
    direccion = fields.Char(related="partner_id.contact_address_inline", string="Dirección")
    estado_cliente = fields.Selection(
        related="partner_id.x_insa_estado_ws",
        string="Estado",
        help="R-08: la plantilla muestra a todos los clientes del reparto; "
        "solo los 'Activo' entran en la hoja de ruta del día.",
    )
    secuencia = fields.Integer(
        string="Posición",
        default=0,
        help="0 = sin ordenar. 1..N = lugar en la fila del reparto ese "
        "día — se arrastra desde el handle para insertar en la posición "
        "de otro cliente (RN-26).",
    )
    activo = fields.Boolean(
        default=True,
        help="Desmarcar cuando el cliente deja de ser parte de este "
        "reparto/día (se fue, cambió de día) sin perder el historial de "
        "la línea. Distinto de 'sin ordenar': un cliente sin ordenar "
        "sigue activo, solo no tiene lugar asignado todavía.",
    )

    def action_ordenar(self):
        """'Seleccionar' un cliente de 'sin ordenar' y mandarlo al final
        de 'ordenados' — después se lo arrastra a su lugar real con el
        handle de secuencia."""
        for rec in self:
            maximo = max(rec.plantilla_id.line_ordenadas_ids.mapped("secuencia") or [0])
            rec.secuencia = maximo + 1
        return True

    def action_quitar(self):
        """C-18: 'Quitar' vuelve el cliente a 'sin ordenar' — no lo saca
        de la plantilla, solo le saca el lugar en la fila."""
        self.write({"secuencia": 0})
        return True
