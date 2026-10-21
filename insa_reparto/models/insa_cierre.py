# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""Cierre de la hoja de ruta por la administrativa (reunión 14/09).

R-04: el repartidor deposita el efectivo en un cajero de depósito (sin
vínculo con el sistema), recibe un ticket con el detalle de billetes y se
lo lleva a Camila, que lo carga acá por denominación.

R-07: toda diferencia con la que se finaliza un reparto queda en un
diario de pérdidas con su motivo — "sería bueno, para tomar acciones".
"""
from odoo import Command, _, api, fields, models

TIPOS_PERDIDA = [
    ("articulo", "Artículo"),
    ("envase", "Envase"),
    ("fallado", "Producto no conforme"),
    ("efectivo", "Efectivo"),
]

# Billetes en circulación, de mayor a menor, como los lista el ticket del
# cajero. Las monedas y los billetes chicos se agregan como línea aparte.
DENOMINACIONES = (20000, 10000, 2000, 1000, 500, 200, 100)


class InsaDeclaracionEfectivo(models.Model):
    _name = "insa.declaracion.efectivo"
    _description = "Línea de la declaración de efectivo (ticket del cajero)"
    _order = "denominacion desc"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute", required=True, ondelete="cascade"
    )
    # 4.7 (revisión 29/09): doble control del efectivo — mismo arqueo por
    # denominación, dos partes distintas lo cargan por separado. 'rol'
    # distingue cuál lista es cuál, mismo patrón que control_salida_ids/
    # control_entrada_ids (un domain por O2M, ver fsm.route.dayroute).
    rol = fields.Selection(
        selection=[("declarante", "Declarante"), ("controlador", "Controlador")],
        required=True,
        default="declarante",
    )
    denominacion = fields.Float(string="Denominación", required=True)
    cantidad = fields.Integer(string="Cantidad")
    subtotal = fields.Float(compute="_compute_subtotal", store=True)

    @api.depends("denominacion", "cantidad")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.denominacion * rec.cantidad


class InsaDeclararEfectivoWizard(models.TransientModel):
    _name = "insa.declarar.efectivo.wizard"
    _description = "Declarar efectivo por denominación"

    hoja_ruta_id = fields.Many2one(comodel_name="fsm.route.dayroute", required=True)
    linea_ids = fields.One2many(
        comodel_name="insa.declarar.efectivo.wizard.linea",
        inverse_name="wizard_id",
        string="Billetes",
    )
    total_declarado = fields.Float(string="Total declarado", compute="_compute_totales")
    total_cobrado = fields.Float(
        string="Efectivo cobrado",
        related="hoja_ruta_id.total_efectivo_registrado",
        help="Σ de los cobros en efectivo que el repartidor registró en "
        "las visitas del día.",
    )
    diferencia = fields.Float(string="Diferencia", compute="_compute_totales")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        hoja = self.env["fsm.route.dayroute"].browse(res.get("hoja_ruta_id"))
        if "linea_ids" in fields_list:
            # Si ya se declaró antes (se está corrigiendo), se parte de lo
            # que se cargó; si no, de los billetes vigentes en cero.
            cargado = {l.denominacion: l.cantidad for l in hoja.declaracion_efectivo_ids}
            denominaciones = sorted(set(DENOMINACIONES) | set(cargado), reverse=True)
            res["linea_ids"] = [
                Command.create({"denominacion": d, "cantidad": cargado.get(d, 0)})
                for d in denominaciones
            ]
        return res

    @api.depends("linea_ids.subtotal", "total_cobrado")
    def _compute_totales(self):
        for rec in self:
            rec.total_declarado = sum(rec.linea_ids.mapped("subtotal"))
            rec.diferencia = rec.total_cobrado - rec.total_declarado

    def action_confirmar(self):
        self.ensure_one()
        self.hoja_ruta_id._declarar_efectivo(
            [(linea.denominacion, linea.cantidad) for linea in self.linea_ids]
        )
        return {"type": "ir.actions.act_window_close"}


class InsaDeclararEfectivoWizardLinea(models.TransientModel):
    _name = "insa.declarar.efectivo.wizard.linea"
    _description = "Billete de la declaración de efectivo"
    _order = "denominacion desc"

    wizard_id = fields.Many2one(
        comodel_name="insa.declarar.efectivo.wizard", required=True, ondelete="cascade"
    )
    denominacion = fields.Float(string="Denominación", required=True)
    cantidad = fields.Integer(string="Cantidad")
    subtotal = fields.Float(compute="_compute_subtotal")

    @api.depends("denominacion", "cantidad")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.denominacion * rec.cantidad


class InsaVerificarEfectivoWizard(models.TransientModel):
    """4.7 (revisión 29/09): segundo control del efectivo, independiente
    de la declaración de Camila — mismo arqueo por denominación, pero lo
    hace el controlador de playa. Mismo patrón que la aprobación del
    control de playa (C-08/C-09, insa.aprobar.control.playa.wizard): el
    controlador no tiene sesión propia de Odoo — solo su usuario y
    contraseña, verificados en el momento con res.users.authenticate().
    Quien abre este wizard (típicamente la administrativa, en el
    mostrador) es quien ya tiene acceso a la hoja; el controlador solo
    acredita su identidad."""

    _name = "insa.verificar.efectivo.wizard"
    _description = "Verificar efectivo contado (segundo control)"

    hoja_ruta_id = fields.Many2one(comodel_name="fsm.route.dayroute", required=True)
    login = fields.Char(string="Usuario del controlador", required=True)
    password = fields.Char(string="Contraseña", required=True)
    linea_ids = fields.One2many(
        comodel_name="insa.verificar.efectivo.wizard.linea",
        inverse_name="wizard_id",
        string="Billetes",
    )
    total_contado = fields.Float(string="Total contado", compute="_compute_totales")
    total_declarado = fields.Float(
        string="Declarado por Administrativa",
        related="hoja_ruta_id.efectivo_caja_repartidor",
    )
    diferencia = fields.Float(string="Diferencia", compute="_compute_totales")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        hoja = self.env["fsm.route.dayroute"].browse(res.get("hoja_ruta_id"))
        if "linea_ids" in fields_list:
            cargado = {
                l.denominacion: l.cantidad
                for l in hoja.declaracion_efectivo_ids
                if l.rol == "controlador"
            }
            denominaciones = sorted(set(DENOMINACIONES) | set(cargado), reverse=True)
            res["linea_ids"] = [
                Command.create({"denominacion": d, "cantidad": cargado.get(d, 0)})
                for d in denominaciones
            ]
        return res

    @api.depends("linea_ids.subtotal", "total_declarado")
    def _compute_totales(self):
        for rec in self:
            rec.total_contado = sum(rec.linea_ids.mapped("subtotal"))
            rec.diferencia = rec.total_declarado - rec.total_contado

    def action_confirmar(self):
        self.ensure_one()
        self.hoja_ruta_id._verificar_efectivo_controlador(
            self.login,
            self.password,
            [(linea.denominacion, linea.cantidad) for linea in self.linea_ids],
        )
        return {"type": "ir.actions.act_window_close"}


class InsaVerificarEfectivoWizardLinea(models.TransientModel):
    _name = "insa.verificar.efectivo.wizard.linea"
    _description = "Billete de la verificación de efectivo"
    _order = "denominacion desc"

    wizard_id = fields.Many2one(
        comodel_name="insa.verificar.efectivo.wizard", required=True, ondelete="cascade"
    )
    denominacion = fields.Float(string="Denominación", required=True)
    cantidad = fields.Integer(string="Cantidad")
    subtotal = fields.Float(compute="_compute_subtotal")

    @api.depends("denominacion", "cantidad")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.denominacion * rec.cantidad


class InsaRepartoPerdidaMotivo(models.Model):
    _name = "insa.reparto.perdida.motivo"
    _description = "Motivo de pérdida del reparto"
    _order = "sequence, id"

    name = fields.Char(string="Motivo", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class InsaRepartoPerdida(models.Model):
    _name = "insa.reparto.perdida"
    _description = "Pérdida registrada al finalizar un reparto"
    _order = "fecha desc, id desc"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute",
        string="Hoja de ruta",
        required=True,
        ondelete="cascade",
    )
    fecha = fields.Date(related="hoja_ruta_id.date", store=True, string="Fecha")
    route_id = fields.Many2one(
        related="hoja_ruta_id.route_id", store=True, string="Reparto"
    )
    tipo = fields.Selection(selection=TIPOS_PERDIDA, required=True)
    product_id = fields.Many2one(comodel_name="product.template", string="Producto")
    cantidad = fields.Float(
        help="Positivo: faltante. Negativo: sobrante. Vacío en efectivo."
    )
    importe = fields.Float(
        help="En efectivo, la diferencia entre lo cobrado y lo declarado. "
        "En productos, una estimación a precio de lista que la "
        "administrativa puede corregir al finalizar."
    )
    motivo_id = fields.Many2one(
        comodel_name="insa.reparto.perdida.motivo",
        string="Motivo",
        required=True,
        ondelete="restrict",
    )
    observacion = fields.Text(string="Observación")
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Registrado por",
        default=lambda self: self.env.user,
        readonly=True,
    )

    @api.depends("hoja_ruta_id", "motivo_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = _("%(hoja)s — %(motivo)s") % {
                "hoja": rec.hoja_ruta_id.display_name or "",
                "motivo": rec.motivo_id.name or "",
            }
