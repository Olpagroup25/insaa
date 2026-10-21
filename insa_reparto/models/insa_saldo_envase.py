# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import api, fields, models


class InsaSaldoEnvase(models.Model):
    _name = "insa.saldo.envase"
    _description = "Saldo de envases por cliente"
    _rec_name = "product_id"

    partner_id = fields.Many2one(
        comodel_name="res.partner", string="Cliente", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto retornable",
        required=True,
        domain=[("es_retornable", "=", True)],
    )
    saldo = fields.Float(
        string="Saldo de envases",
        compute="_compute_saldo",
        help="Cantidad del envase asociado en la ubicación de stock del cliente.",
    )
    consumo_promedio = fields.Float(
        string="Consumo promedio",
        help="Ingresado manualmente hasta contar con historial real de "
        "consumo (a completar durante la migración de datos).",
    )
    tolerancia = fields.Float(
        related="product_id.tolerancia_envase", string="Tolerancia (%)"
    )
    permitido = fields.Float(string="Permitido", compute="_compute_permitido")
    cantidad_a_recuperar = fields.Float(
        string="Cantidad a recuperar", compute="_compute_permitido"
    )
    # 4.6 (revisión 29/09): indicador aparte para "a favor" — sin tocar
    # cantidad_a_recuperar, que ya usa la app móvil y nunca da negativo
    # (está recortada en cero a propósito, ver _compute_permitido).
    estado_saldo = fields.Selection(
        selection=[("debe", "Debe"), ("a_favor", "A favor"), ("al_dia", "Al día")],
        string="Estado",
        compute="_compute_estado_saldo",
        store=True,
    )

    @api.depends("saldo", "permitido")
    def _compute_estado_saldo(self):
        for rec in self:
            diferencia = rec.saldo - rec.permitido
            if diferencia > 0:
                rec.estado_saldo = "debe"
            elif diferencia < 0:
                rec.estado_saldo = "a_favor"
            else:
                rec.estado_saldo = "al_dia"

    @api.depends("partner_id.property_stock_customer", "product_id.envase_id")
    def _compute_saldo(self):
        for rec in self:
            envase = rec.product_id.envase_id
            location = rec.partner_id.property_stock_customer
            if not envase or not location:
                rec.saldo = 0.0
                continue
            quants = self.env["stock.quant"].search(
                [
                    ("product_id.product_tmpl_id", "=", envase.id),
                    ("location_id", "=", location.id),
                ]
            )
            rec.saldo = sum(quants.mapped("quantity"))

    @api.depends("consumo_promedio", "tolerancia", "saldo")
    def _compute_permitido(self):
        for rec in self:
            rec.permitido = round(rec.consumo_promedio * (1 + rec.tolerancia / 100.0))
            rec.cantidad_a_recuperar = max(rec.saldo - rec.permitido, 0.0)
