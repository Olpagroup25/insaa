# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    codigo_distribuidor = fields.Char(
        string="Código de distribuidor",
        help="Identificador interno del repartidor usado por INSA "
        "(ej: \"distribuidor número 9\"). Es un atributo del empleado, "
        "constante entre sus distintos repartos.",
    )
