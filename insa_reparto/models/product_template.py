# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    es_retornable = fields.Boolean(
        string="Es retornable",
        help="Identifica productos que llevan un envase asociado "
        "(bidones, sifones). El envase se modela como un producto "
        "separado vinculado en 'Producto envase'.",
    )
    es_envase = fields.Boolean(
        string="Es envase",
        help="Marca el producto que representa el envase/continente "
        "(bidón vacío, sifón vacío): no se vende ni se factura por "
        "separado, solo circula como activo retornable de INSA. Sin "
        "este flag, un envase es indistinguible de un descartable "
        "(ambos tienen es_retornable = False).",
    )
    envase_id = fields.Many2one(
        comodel_name="product.template",
        string="Producto envase",
        domain=[("es_envase", "=", True)],
        help="Producto que representa el envase de este artículo. "
        "El contenido y el envase son dos productos separados: el "
        "movimiento del envase lo genera automáticamente el registro "
        "de movimiento por cliente, no una venta de kit.",
    )
    visible_control_playa = fields.Boolean(
        string="Visible en control de playa",
        default=True,
    )
    exportable_movil = fields.Boolean(
        string="Exportable a app móvil",
        default=True,
        help="Reservado para D3 (app móvil del repartidor).",
    )
    frecuente = fields.Boolean(string="Producto frecuente")
    tolerancia_envase = fields.Float(
        string="Tolerancia de envase (%)",
        default=50.0,
        help="Porcentaje sobre el consumo promedio del cliente que se "
        "considera cantidad permitida de envases en su poder.",
    )

    @api.constrains("es_retornable", "es_envase")
    def _check_no_retornable_y_envase_a_la_vez(self):
        for rec in self:
            if rec.es_retornable and rec.es_envase:
                raise ValidationError(
                    _(
                        "%s no puede ser retornable (el artículo/contenido) y "
                        "envase (el continente) al mismo tiempo: son dos "
                        "productos distintos vinculados por 'Producto envase'."
                    )
                    % rec.name
                )

    @api.constrains("es_retornable", "envase_id")
    def _check_envase_es_storable(self):
        for rec in self:
            if not rec.es_retornable:
                continue
            if not rec.envase_id:
                raise ValidationError(
                    _("%s es retornable pero no tiene un producto envase vinculado.")
                    % rec.name
                )
            if not rec.envase_id.es_envase:
                raise ValidationError(
                    _(
                        "El producto vinculado '%s' debe tener marcado 'Es "
                        "envase' para poder usarse como envase de '%s'."
                    )
                    % (rec.envase_id.name, rec.name)
                )
            if not rec.envase_id.is_storable:
                raise ValidationError(
                    _(
                        "El envase '%s' debe tener 'Track Inventory' (is_storable) "
                        "activado — si no, el saldo de envases por cliente no se "
                        "puede calcular porque Odoo no genera movimientos de stock "
                        "reales para productos no rastreados."
                    )
                    % rec.envase_id.name
                )
