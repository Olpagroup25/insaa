# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""4.3 (revisión 29/09): pedido habitual del cliente, con productos reales
en vez del texto libre de insa_order_notes (que se mantiene de solo
lectura, de referencia — lo siguen llenando el alta web y la app)."""
from odoo import fields, models


class InsaPedidoHabitualLinea(models.Model):
    _name = "insa.pedido.habitual.linea"
    _description = "Línea del pedido habitual de un cliente"

    partner_id = fields.Many2one(
        comodel_name="res.partner", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto",
        required=True,
        domain="[('sale_ok', '=', True), ('es_envase', '=', False)]",
    )
    cantidad = fields.Float(string="Cantidad", default=1.0)
