# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""R-6.4: registro de auditoría de toda diferencia que quedó sin
resolver y se cerró igual (RN-14, "toda liquidación siempre se
cierra"). Se genera automáticamente desde
fsm.route.dayroute._finalizar_con_diferencias — no se crea a mano."""
from odoo import fields, models


class InsaAjusteInventario(models.Model):
    _name = "insa.ajuste.inventario"
    _description = "Ajuste de inventario por diferencia no resuelta (R-6.4)"
    _order = "fecha desc"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute", required=True, ondelete="cascade"
    )
    tipo = fields.Selection(
        selection=[
            ("articulo", "Artículo"),
            ("envase", "Envase"),
            ("fallado", "Fallado (discordancia)"),
        ],
        required=True,
    )
    product_id = fields.Many2one(
        comodel_name="product.template", string="Producto", required=True
    )
    diferencia = fields.Float(
        string="Diferencia ajustada",
        help="Valor de 'diferencia' de la liquidación en el momento del "
        "cierre forzado.",
    )
    motivo = fields.Text(string="Motivo del cierre forzado")
    usuario_id = fields.Many2one(
        comodel_name="res.users",
        string="Autorizado por",
        default=lambda self: self.env.user,
        readonly=True,
    )
    fecha = fields.Datetime(default=fields.Datetime.now, readonly=True)
    stock_move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Movimiento de stock generado",
        readonly=True,
        help="Solo se completa para envases: es el movimiento que corrige "
        "la cantidad física en el camión. Para artículos queda vacío — "
        "no se genera movimiento (ver _generar_ajustes_inventario).",
    )
