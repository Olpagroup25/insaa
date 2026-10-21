# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""Revisión 06/10: gastos del reparto (combustible, peaje, comida,
estacionamiento, mantenimiento, otro) — pantalla ya existente en la app
("Elegir tipo") sin backend conectado hasta ahora. Se cargan libremente
desde la app, sin aprobación (RN a confirmar si hace falta más adelante),
y se descuentan del efectivo esperado en Liquidación: si el repartidor
pagó un peaje con la plata que cobró, lo que trae de vuelta es
legítimamente menor al total cobrado — sin este descuento, esa diferencia
se veía como un faltante."""
from odoo import fields, models


class InsaGastoReparto(models.Model):
    _name = "insa.gasto.reparto"
    _description = "Gasto cargado por el repartidor durante el reparto"
    _order = "fecha_hora desc"

    hoja_ruta_id = fields.Many2one(
        comodel_name="fsm.route.dayroute",
        string="Hoja de ruta",
        required=True,
        ondelete="cascade",
    )
    tipo = fields.Selection(
        selection=[
            ("combustible", "Combustible"),
            ("peaje", "Peaje"),
            ("libreria", "Librería"),
            ("gomeria", "Gomería"),
            ("repuestos", "Repuestos"),
            ("reparaciones", "Reparaciones"),
            ("lavado", "Lavado"),
            ("gastos_personales", "Gastos Personales"),
        ],
        required=True,
    )
    importe = fields.Float(required=True)
    comprobante = fields.Image(string="Comprobante", max_width=1920, max_height=1920)
    observacion = fields.Char(string="Observación")
    fecha_hora = fields.Datetime(default=fields.Datetime.now, required=True)
