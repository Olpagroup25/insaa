# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import fields, models


class InsaCity(models.Model):
    _name = 'insa.city'
    _description = 'Ciudad o Barrio (Insa)'
    _order = 'name'

    name = fields.Char(string='Ciudad o Barrio', required=True)
    active = fields.Boolean(string='Activo', default=True)
    street_ids = fields.One2many('insa.street', 'city_id', string='Calles')
