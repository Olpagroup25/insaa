# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import fields, models


class InsaStreet(models.Model):
    _name = 'insa.street'
    _description = 'Calle (Insa)'
    _order = 'name'

    name = fields.Char(string='Calle', required=True)
    city_id = fields.Many2one('insa.city', string='Ciudad o Barrio', required=True, ondelete='cascade')
    active = fields.Boolean(string='Activo', default=True)
