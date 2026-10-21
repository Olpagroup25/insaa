# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""RN-17: la administración puede inyectar un pedido fuera de ruta a una
hoja de ruta ya en curso — no corresponde al listado de clientes del
repartidor del día, pero tiene que aparecerle igual para que lo visite."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class InsaPedidoExtra(models.Model):
    _name = "insa.pedido.extra"
    _description = "Pedido extra inyectado por administración"
    _order = "fecha_hora"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute",
        string="Hoja de ruta",
        required=True,
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner", string="Cliente", required=True
    )
    fecha_hora = fields.Datetime(default=fields.Datetime.now, required=True)
    creado_por_id = fields.Many2one(
        comodel_name="res.users",
        string="Cargado por",
        default=lambda self: self.env.user,
        readonly=True,
    )
    nota = fields.Text(
        string="Observación",
        help="Dato heredado: antes el pedido se anotaba acá en texto libre. "
        "Ahora se carga con productos y cantidades en 'Qué pidió'.",
    )
    resumen = fields.Char(
        string="Qué pidió",
        compute="_compute_resumen",
        help="Productos y cantidades pedidos. Si el pedido es anterior al "
        "cambio y solo tiene texto libre, muestra ese texto.",
    )
    linea_ids = fields.One2many(
        comodel_name="insa.pedido.extra.linea",
        inverse_name="pedido_extra_id",
        string="Artículos solicitados",
    )
    atendido = fields.Boolean(
        string="Atendido",
        default=False,
        help="El repartidor visitó al cliente y resolvió el pedido.",
    )

    @api.depends("linea_ids.product_id", "linea_ids.cantidad", "nota")
    def _compute_resumen(self):
        for rec in self:
            if rec.linea_ids:
                rec.resumen = ", ".join(
                    "%g × %s" % (lin.cantidad, lin.product_id.display_name)
                    for lin in rec.linea_ids
                )
            else:
                rec.resumen = rec.nota or ""

    @api.constrains("hoja_ruta_id")
    def _check_ruta_en_curso(self):
        # RN-17: solo tiene sentido inyectar un pedido a una ruta que ya
        # está en la calle (no antes de empezar, no después de cerrada).
        for rec in self:
            if rec.hoja_ruta_id.state not in ("en_reparto", "consolidando"):
                raise ValidationError(
                    _(
                        "Solo se puede cargar un pedido extra en una hoja de "
                        "ruta que está en curso (En reparto/Consolidando)."
                    )
                )


class InsaPedidoExtraLinea(models.Model):
    _name = "insa.pedido.extra.linea"
    _description = "Línea de artículo de un pedido extra"

    pedido_extra_id = fields.Many2one(
        comodel_name="insa.pedido.extra", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto",
        required=True,
        domain="[('sale_ok', '=', True), ('es_envase', '=', False)]",
    )
    cantidad = fields.Float(string="Cantidad", default=1.0)
